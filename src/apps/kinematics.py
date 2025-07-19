import os
import glob
import logging
import json
from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import compile_variant, generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines
# Importações para o modo de poda e profiler fake
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação Kinematics
KINEMATICS_CONFIG = {
    # Arquivos específicos da aplicação
    "original_file": "axbench/applications/inversek2j/src/kinematics.cpp",
    "inversek2j_main": "axbench/applications/inversek2j/src/inversek2j.cpp",
    "inversek2j_object": "axbench/applications/inversek2j/inversek2j.o",
    "train_data_input": "axbench/applications/inversek2j/train.data/input/1k.data",
    
    # Padrões de arquivos
    "source_pattern": "kinematics_*.cpp",
    "exe_prefix": "kinematics_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    
    # Parâmetros de geração de variantes
    "input_file_for_variants": "axbench/applications/inversek2j/src/kinematics.cpp",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    "compile_files": lambda variant_file, config: [
        config["inversek2j_main"],  # sempre o main
        variant_file                # a variante de kinematics
    ],
}

def prepare_environment(config):
    """Prepara o ambiente específico para a aplicação kinematics"""
    # Copia approx.h normalmente...
    approx_source = config.get("approx_file", "data/reference/approx.h")
    copy_file(approx_source, config["input_dir"])

    # Compila o original e salva o .o no executables_dir do workspace
    original_cpp = config["original_file"]
    output_o = os.path.join(config["executables_dir"], "inversek2j.o")
    os.makedirs(config["executables_dir"], exist_ok=True)
    compile_cmd = f"riscv32-unknown-elf-g++ -I{config['input_dir']} -c {original_cpp} -o {output_o}"
    result = os.system(compile_cmd)
    if result != 0:
        logging.error("Falha ao compilar o objeto original para o workspace.")
        return False

    # Atualiza o config para apontar para o novo .o
    config["inversek2j_object"] = os.path.join(config["executables_dir"], "inversek2j.o")
    return True

def generate_variants(base_config):
    """Gera variantes específicas para kinematics"""
    from generator import generate_variants as gen_vars
    # ... (código existente) ...
    
    # Configura o gerador para kinematics
    config = {**base_config, **KINEMATICS_CONFIG}
    
    print(f"Gerando variantes para kinematics a partir de {config['input_file_for_variants']}")
    
    # Obtém os dados do arquivo original
    lines, modifiable_lines, physical_to_logical = parse_code(config["input_file_for_variants"])
    
    # Gera as variantes
    return gen_vars(
        lines, 
        modifiable_lines, 
        physical_to_logical, 
        config["operations_map"], 
        config["input_dir"], 
        os.path.basename(config["input_file_for_variants"]),
        config["executed_variants_file"]
    )

# --- NOVAS FUNÇÕES E FUNÇÕES MODIFICADAS ---

def get_pruning_config(base_config):
    """Retorna a configuração necessária para o algoritmo de poda de árvore."""
    config = {**base_config, **KINEMATICS_CONFIG}
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
    exe_prefix = config.get("exe_prefix", "kinematics_")
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
        logging.info(f"[Variante {variant_id}] Executando Prof5 (Fake)...")
        
        instrucoes = contar_instrucoes_log(spike_log_file)
        if not instrucoes:
            logging.error(f"[Variante {variant_id}] Falha ao contar instruções do log do Spike.")
            return None

        resultados = avaliar_modelo_energia(instrucoes, prof5_model)
        summary = resultados["summary"]
        tempo_total = summary["latency_ms"]  # ou "latency_seconds" se preferir segundos

        with open(prof5_time_file, 'w') as f:
            f.write(f"{tempo_total}\n")
        os.chmod(prof5_time_file, 0o666)

        with open(prof5_report_path, 'w') as f:
            json.dump(resultados, f, indent=2)
        
        logging.info(f"[Variante {variant_id}] Prof5 (Fake) concluído. Tempo estimado: {tempo_total:.4f}ms")
        return tempo_total
        
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro na execução do Prof5 (Fake): {e}", exc_info=True)
        status_monitor.update_status(variant_id, "Erro no Profiling (Fake)")
        return None

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Executa apenas a parte de profiling (Prof5 Fake) da simulação."""
    config = {**base_config, **KINEMATICS_CONFIG}
    
    prof5_time = run_prof5_fake(
        resume_context["spike_log_file"],
        config["prof5_model"],
        resume_context["prof5_time_file"],
        resume_context["prof5_report_path"],
        resume_context["variant_id"],
        status_monitor
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

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    """Simula uma variante, usando o workspace correto e o profiler fake."""
    config = {**KINEMATICS_CONFIG, **base_config}  # Troque a ordem!
    
    is_original = (variant_file == config["original_file"])
    variant_id = "original" if is_original else short_hash(variant_hash)
    
    # Todos os caminhos são construídos a partir de `base_config` (que é o `execution_config`)
    # garantindo que tudo seja salvo no workspace da execução atual.
    exe_file = os.path.join(config["executables_dir"], f"{config['exe_prefix']}{variant_hash}")
    output_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['output_suffix']}")
    spike_log_file = os.path.join(config["logs_dir"], f"{config['exe_prefix']}{variant_hash}.log")
    prof5_time_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['prof5_suffix']}")
    prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
    dump_file = os.path.join(config["dump_dir"], f"dump_{variant_hash}.txt")
    
    if not compile_variant(variant_file, variant_hash, config, status_monitor):
        return (None, None)
    
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
        return (None, None)
    
    sim_time = run_spike_simulation(exe_file, config["train_data_input"], output_file, spike_log_file, variant_id, status_monitor)
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

    # Execução completa (Força Bruta ou nó raiz da Árvore)
    success = run_profiling_stage(resume_context, base_config, status_monitor)
    
    if success:
        status_monitor.update_status(variant_id, "Concluída")
        # Retorna o caminho do arquivo de saída para o nó raiz e um valor "True" para o modo força bruta
        return (output_file, None)
    else:
        status_monitor.update_status(variant_id, "Falha no Profiling")
        return (None, None)

def find_variants_to_simulate(base_config):
    """Identifica as variantes do kinematics que precisam ser simuladas"""
    # Configuração completa (base + específica)
    config = {**base_config, **KINEMATICS_CONFIG}
    
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
