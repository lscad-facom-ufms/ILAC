import os
import glob
import logging
import subprocess
import json
from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines
# Importações para o modo de poda e profiler fake
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação FFT
FFT_CONFIG = {
    # Arquivos específicos da aplicação
    "original_file": "axbench/applications/fft/src/fourier.cpp",
    "fft_object": "axbench/applications/fft/fft.o",
    "train_data_input": "512",  # Modifique para o valor 512 em vez de um arquivo
    
    # Padrões de arquivos
    "source_pattern": "fourier_*.cpp",  # Procurando por .cpp
    "exe_prefix": "fourier_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    
    # Parâmetros de geração de variantes
    "input_file_for_variants": "axbench/applications/fft/src/fourier.cpp",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    
    # Parâmetros específicos de compilação do FFT
    "include_dir": "axbench/applications/fft/src",
    "additional_sources": [
        "axbench/applications/fft/src/fft.cpp",
        "axbench/applications/fft/src/complex.cpp"
    ],
    "optimization_level": "-O"
}

def prepare_environment(base_config):
    """Prepara o ambiente específico para a aplicação FFT"""
    # Copia o arquivo approx.h para o diretório de input
    approx_source = base_config.get("approx_file", "data/reference/approx.h")
    return copy_file(approx_source, base_config["input_dir"])

def generate_variants(base_config):
    """Gera variantes específicas para FFT"""
    from gera_variantes import main as gera_main
    
    # Configura o gerador para FFT
    config = {**base_config, **FFT_CONFIG}
    
    print(f"Gerando variantes para FFT a partir de {config['input_file_for_variants']}")
    
    # Configuração específica para FFT
    config_override = {
        "input_file": config["input_file_for_variants"],
        "operations_map": config["operations_map"],
        "executed_variants_file": config["executed_variants_file"]
    }
    
    # Chama a função main do gera_variantes.py com a configuração personalizada
    return gera_main(config_override)

def get_pruning_config(base_config):
    """Retorna a configuração necessária para o algoritmo de poda de árvore."""
    config = {**base_config, **FFT_CONFIG}
    source_file = config["input_file_for_variants"]
    original_lines, modifiable_lines, physical_to_logical = parse_code(source_file)
    return {
        "source_file": source_file,
        "original_lines": original_lines,
        "modifiable_lines": modifiable_lines,
        "physical_to_logical": physical_to_logical,
        "app_specific_config": config
    }

def generate_specific_variant(original_lines, physical_to_logical, modified_line_indices, config):
    """Gera um único arquivo de variante com base em um conjunto específico de linhas modificadas."""
    modified_lines_content = list(original_lines)
    for idx in modified_line_indices:
        modified_lines_content[idx] = apply_transformation(modified_lines_content[idx], config["operations_map"])

    variant_hash = gerar_hash_codigo_logico(modified_lines_content, physical_to_logical)
    
    variant_dir = config.get("input_dir")
    base_name, ext = os.path.splitext(os.path.basename(config["input_file_for_variants"]))
    variant_filepath = os.path.join(variant_dir, f"{base_name}_{variant_hash}{ext}")

    with open(variant_filepath, 'w') as f:
        f.writelines(modified_lines_content)
        
    return variant_filepath, variant_hash

def cleanup_variant_files(variant_hash, config):
    """Remove arquivos temporários de uma variante (log e dump)."""
    exe_prefix = config.get("exe_prefix", "fourier_")
    spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(config["dump_dir"], f"dump_{variant_hash}.txt")
    
    for f in [spike_log_file, dump_file]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError as e:
                logging.error(f"Erro ao remover arquivo temporário {f}: {e}")

def run_prof5_fake(spike_log_file, prof5_model, prof5_time_file, prof5_report_path, variant_id, status_monitor):
    """Executa o prof5fake para análise de energia e performance."""
    try:
        status_monitor.update_status(variant_id, "Profiling (Fake)")
        instrucoes = contar_instrucoes_log(spike_log_file)
        if not instrucoes:
            return None
        energia_total, tempo_total, report_data = avaliar_modelo_energia(instrucoes, prof5_model)
        with open(prof5_time_file, 'w') as f:
            f.write(f"{tempo_total}\n")
        os.chmod(prof5_time_file, 0o666)
        with open(prof5_report_path, 'w') as f:
            json.dump(report_data, f, indent=2)
        return tempo_total
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5 (Fake): {e}", exc_info=True)
        return None

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Executa apenas a parte de profiling (Prof5 Fake) da simulação."""
    config = {**base_config, **FFT_CONFIG}
    prof5_time = run_prof5_fake(
        resume_context["spike_log_file"], config["prof5_model"],
        resume_context["prof5_time_file"], resume_context["prof5_report_path"],
        resume_context["variant_id"], status_monitor
    )

    if prof5_time is not None:
        # Salva os índices das linhas modificadas em um arquivo .txt
        # para consistência entre os modos de execução.
        try:
            variant_filepath = resume_context["variant_file"]
            original_filepath = config["original_file"]
            variant_hash = resume_context["variant_hash"]

            with open(variant_filepath, 'r') as f_variant, open(original_filepath, 'r') as f_original:
                variant_lines = f_variant.readlines()
                original_lines = f_original.readlines()

            modified_indices = [i for i, (line1, line2) in enumerate(zip(original_lines, variant_lines)) if line1 != line2]
            
            save_modified_lines_txt(modified_indices, variant_hash, config)
        except Exception as e:
            logging.error(f"[Variante {resume_context['variant_id']}] Falha ao salvar índices de linhas modificadas: {e}")

    cleanup_variant_files(resume_context["variant_hash"], config)
    return prof5_time is not None

def find_variants_to_simulate(base_config):
    """Identifica as variantes que precisam ser simuladas"""
    # Configuração completa
    config = {**base_config, **FFT_CONFIG}
    
    # Carrega as variantes já executadas
    executed_variants = load_executed_variants(config["executed_variants_file"])
    
    # Lista para armazenar variantes a serem simuladas
    variants_to_simulate = []
    
    # Primeiro verifica o arquivo original
    with open(config["original_file"], "r") as f:
        original_lines = f.readlines()
    
    _, __, original_physical_to_logical = parse_code(config["original_file"])
    original_hash = gerar_hash_codigo_logico(original_lines, original_physical_to_logical)
    
    # Adiciona o arquivo original à lista se ainda não tiver sido executado
    if original_hash not in executed_variants:
        variants_to_simulate.append((config["original_file"], original_hash))
        print(f"Versão original será simulada (hash: {short_hash(original_hash)})")
    else:
        print(f"Versão original já foi executada (hash: {short_hash(original_hash)})")
    
    # Processa cada arquivo de variante no diretório
    pattern = os.path.join(config["input_dir"], config["source_pattern"])
    print(f"Buscando variantes em: {pattern}")
    for file in glob.glob(pattern):
        with open(file, "r") as f:
            variant_lines = f.readlines()
        
        variant_hash = gerar_hash_codigo_logico(variant_lines, original_physical_to_logical)
        
        if variant_hash not in executed_variants:
            variants_to_simulate.append((file, variant_hash))
            print(f"Variante {os.path.basename(file)} será simulada (hash: {short_hash(variant_hash)})")
        else:
            print(f"Variante {os.path.basename(file)} já foi executada (hash: {short_hash(variant_hash)})")
    
    return variants_to_simulate, original_physical_to_logical

def compile_fft_variant(variant_file, variant_hash, config, status_monitor):
    """
    Compilação especializada para a aplicação FFT com múltiplos arquivos fonte.
    """
    is_original = (variant_file == config["original_file"])
    variant_id = "original" if is_original else short_hash(variant_hash)
    
    # Atualiza o status
    status_monitor.update_status(variant_id, "Compilando")
    
    # Define os arquivos de saída
    exe_prefix = config.get("exe_prefix", "app_")
    exe_file = os.path.join(config["executables_dir"], f"{exe_prefix}{variant_hash}")
    
    # Obtém as opções de compilação
    optimization = config.get("optimization_level", "-O")
    include_dir = config.get("include_dir", "")
    additional_sources = config.get("additional_sources", [])
    
    # Comando de compilação específico para FFT
    compile_cmd = [
        "riscv32-unknown-elf-g++",
        "-Wall", "-Wextra",
        "-march=rv32imafdc",
        optimization
    ]
    
    # Adiciona diretório de include se especificado
    if include_dir:
        compile_cmd.extend(["-I", include_dir])
    
    # Adiciona diretório de input para acessar approx.h
    compile_cmd.extend(["-I", config["input_dir"]])
    
    # Adiciona output
    compile_cmd.extend(["-o", exe_file])
    
    # Adiciona o arquivo da variante e os arquivos adicionais
    compile_cmd.append(variant_file)
    compile_cmd.extend(additional_sources)
    
    # Adiciona biblioteca matemática
    compile_cmd.append("-lm")
    
    # Executa a compilação
    try:
        result = subprocess.run(compile_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.stderr:
            logging.info(f"[Variante {variant_id}] Avisos de compilação: {result.stderr.decode()}")
        logging.info(f"[Variante {variant_id}] Compilação concluída com sucesso")
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro na compilação: {e.stderr.decode()}")
        status_monitor.update_status(variant_id, "Erro na compilação")
        return False
    
    # Define permissões do executável
    os.chmod(exe_file, 0o755)
    
    return True

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    """Simula uma variante específica do FFT, com suporte a execução em duas etapas."""
    config = {**base_config, **FFT_CONFIG}
    
    is_original = (variant_file == config["original_file"])
    variant_id = "original" if is_original else short_hash(variant_hash)
    
    exe_file = os.path.join(config["executables_dir"], f"{config['exe_prefix']}{variant_hash}")
    output_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['output_suffix']}")
    spike_log_file = os.path.join(config["logs_dir"], f"{config['exe_prefix']}{variant_hash}.log")
    prof5_time_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['prof5_suffix']}")
    prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
    dump_file = os.path.join(config["dump_dir"], f"dump_{variant_hash}.txt")
    
    if not compile_fft_variant(variant_file, variant_hash, config, status_monitor):
        return (None, None)
    
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
        return (None, None)
    
    sim_time = run_spike_simulation(
        exe_file, config["train_data_input"], output_file, 
        spike_log_file, variant_id, status_monitor
    )
    if sim_time is None:
        return (None, None)
    
    resume_context = {
        "exe_file": exe_file, "spike_log_file": spike_log_file, "dump_file": dump_file,
        "variant_id": variant_id, "variant_hash": variant_hash,
        "prof5_time_file": prof5_time_file, "prof5_report_path": prof5_report_path,
        "variant_file": variant_file, # Adicionar para uso no profiling_stage
    }

    if only_spike:
        return output_file, resume_context

    success = run_profiling_stage(resume_context, base_config, status_monitor)
    
    if success:
        status_monitor.update_status(variant_id, "Concluída")
        return (output_file, None)
    else:
        status_monitor.update_status(variant_id, "Falha no Profiling")
        return (None, None)

def save_modified_lines_txt(node_modified_lines, variant_hash, config):
    """
    Salva os índices das linhas modificadas (mesmos usados na árvore de poda).
    
    Args:
        node_modified_lines: Lista de índices das linhas modificadas (do nó da árvore)
        variant_hash: Hash da variante
        config: Configuração do sistema
    """
    try:
        # Criar arquivo TXT apenas com os índices das linhas modificadas
        linhas_dir = config.get("linhas_modificadas_dir", "storage/linhas_modificadas")
        txt_filename = f"linhas_{variant_hash}.txt"
        txt_filepath = os.path.join(linhas_dir, txt_filename)
        
        with open(txt_filepath, 'w', encoding='utf-8') as f:
            for line_index in node_modified_lines:
                f.write(f"{line_index}\n")
        
        os.chmod(txt_filepath, 0o666)
        logging.info(f"Índices das linhas modificadas salvos: {txt_filepath}")
        
        return txt_filepath
        
    except Exception as e:
        logging.error(f"Erro ao salvar índices das linhas modificadas: {e}", exc_info=True)
        return None