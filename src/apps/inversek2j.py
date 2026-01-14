import os
import glob
import logging
import subprocess
import json

from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação KINEMATICS (Inversek2j)
KINEMATICS_CONFIG = {
    # Arquivos específicos da aplicação
    "inversek2j_main_file": "data/applications/inversek2j/src/inversek2j.cpp", # O arquivo Main (fixo)
    "kinematics_source_file": "data/applications/inversek2j/src/kinematics.cpp", # O arquivo Kernel (variável)
    "train_data_input": "data/applications/inversek2j/train.data/input/1k.data",

    # Padrões de arquivos para variantes
    "source_pattern": "kinematics_*.cpp", 
    "exe_prefix": "inversek2j_", 
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",

    # Parâmetros de geração de variantes
    "input_file_for_variants": "data/applications/inversek2j/src/kinematics.cpp",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},

    # Parâmetros específicos de compilação
    "include_dir": "data/applications/inversek2j/src",
    "optimization_level": "-O",
}

def cleanup_variant_files(variant_hash, config):
    """Remove arquivos temporários de uma variante (log e dump)."""
    exe_prefix = config.get("exe_prefix", "inversek2j_")
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    
    for f in [spike_log_file, dump_file]:
        if os.path.exists(f):
            try:
                os.remove(f)
                logging.debug(f"Arquivo de variante removido: {f}")
            except OSError as e:
                logging.warning(f"Não foi possível remover o arquivo de variante {f}: {e}")

def run_prof5_fake(spike_log_file, prof5_model, prof5_time_file, prof5_report_path, variant_id, status_monitor):
    """
    Executa o prof5fake (substituto do Prof5) para análise de energia e performance.
    """
    try:
        status_monitor.update_status(variant_id, "Executando Prof5Fake")
        
        if not os.path.exists(spike_log_file):
            logging.error(f"[Variante {variant_id}] Arquivo de log do Spike não encontrado: {spike_log_file}")
            return None
            
        if not os.path.exists(prof5_model):
            logging.error(f"[Variante {variant_id}] Modelo Prof5 não encontrado: {prof5_model}")
            return None
        
        logging.info(f"[Variante {variant_id}] Contando instruções no log do Spike...")
        instrucoes_dict = contar_instrucoes_log(spike_log_file)
        
        if not instrucoes_dict:
            logging.error(f"[Variante {variant_id}] Nenhuma instrução encontrada no log")
            return None
            
        logging.info(f"[Variante {variant_id}] Aplicando modelo de energia...")
        resultados_energia = avaliar_modelo_energia(instrucoes_dict, prof5_model)
        
        if not resultados_energia:
            logging.error(f"[Variante {variant_id}] Falha na avaliação do modelo de energia")
            return None
            
        with open(prof5_report_path, 'w') as f:
            json.dump(resultados_energia, f, indent=2, sort_keys=True)
        os.chmod(prof5_report_path, 0o666)
        
        latency_ms = resultados_energia["summary"]["latency_ms"]
        
        with open(prof5_time_file, 'w') as f:
            f.write(f"{latency_ms}\n")
        os.chmod(prof5_time_file, 0o666)
        
        logging.info(f"[Variante {variant_id}] Prof5Fake concluído - Latência: {latency_ms:.2f} ms")
        status_monitor.update_status(variant_id, "Prof5Fake Concluído")
        
        return latency_ms
        
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5Fake: {e}", exc_info=True)
        status_monitor.update_status(variant_id, "Erro Prof5Fake")
        return None

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
    
    variant_dir = config.get("input_dir", "storage/variantes")
    base_name, ext = os.path.splitext(os.path.basename(config["input_file_for_variants"]))
    variant_filepath = os.path.join(variant_dir, f"{base_name}_{variant_hash}{ext}")

    with open(variant_filepath, 'w') as f:
        f.writelines(modified_lines_content)
        
    return variant_filepath, variant_hash

def prepare_environment(base_config):
    """Prepara o ambiente específico para a aplicação Kinematics"""
    config = {**base_config, **KINEMATICS_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    """Gera variantes específicas para kinematics.cpp usando subprocess"""
    import subprocess
    import os
    
    config = {**base_config, **KINEMATICS_CONFIG}
    logging.debug(f"Gerando em: {config['input_dir']}")
    
    try:
        input_path = os.path.abspath(config["input_file_for_variants"])
        output_path = os.path.abspath(config["input_dir"])
        executados_path = os.path.abspath(config["executed_variants_file"])
        
        cmd = [
            "python3", "src/gera_variantes.py",
            "--input", input_path,
            "--output", output_path,
            "--executados", executados_path
        ]
        
        logging.debug(f"Executando comando de geração: {' '.join(cmd)}")

        current_script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_script_dir, "..", ".."))
        
        logging.debug(f"Diretório de trabalho detectado: {project_root}")

        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
        
        if result.returncode == 0:
            import glob
            pattern = os.path.join(config["input_dir"], "kinematics_*.cpp")
            generated_files = glob.glob(pattern)
            logging.info(f"{len(generated_files)} variantes encontradas/geradas no workspace.")
            return len(generated_files) > 0
        else:
            logging.error(f"Comando de geração falhou com código {result.returncode}")
            logging.error(f"STDERR: {result.stderr}")
            return False
            
    except Exception as e:
        logging.error(f"Erro na geração de variantes: {e}")
        return False

def find_variants_to_simulate(base_config):
    """Identifica as variantes do kinematics que precisam ser simuladas"""
    config = {**base_config, **KINEMATICS_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []
    
    # Mapa lógico do arquivo original
    kinematics_original_path = config["kinematics_source_file"]
    with open(kinematics_original_path, "r") as f:
        original_lines = f.readlines()
    _, __, original_physical_to_logical = parse_code(kinematics_original_path)
    
    # Hash da versão original
    original_hash = gerar_hash_codigo_logico(original_lines, original_physical_to_logical)
    
    if original_hash not in executed_variants:
        variants_to_simulate.append((kinematics_original_path, original_hash))
        logging.info(f"Versão original de KINEMATICS será simulada (hash: {short_hash(original_hash)})")
    else:
        logging.info(f"Versão original de KINEMATICS já foi executada.")

    # Busca variantes
    variant_pattern = os.path.join(config["input_dir"], config["source_pattern"])
    variant_files = glob.glob(variant_pattern)
    
    for variant_file_path in variant_files:
        if os.path.abspath(variant_file_path) == os.path.abspath(kinematics_original_path):
            continue 

        with open(variant_file_path, "r") as f:
            variant_lines = f.readlines()
        
        variant_hash = gerar_hash_codigo_logico(variant_lines, original_physical_to_logical)
        
        if variant_hash not in executed_variants:
            variants_to_simulate.append((variant_file_path, variant_hash))
            logging.info(f"Variante KINEMATICS ({os.path.basename(variant_file_path)}) será simulada (hash: {short_hash(variant_hash)})")
        else:
            logging.info(f"Variante KINEMATICS já executada.")
            
    return variants_to_simulate, original_physical_to_logical

def compile_kinematics_variant(main_cpp, kernel_cpp, output_naming_hash, config, status_monitor):
    """Compilação especializada: inversek2j.cpp (fixo) + kinematics.cpp (variável)."""
    
    is_original = (os.path.abspath(kernel_cpp) == os.path.abspath(config["kinematics_source_file"]))
    variant_id = "original" if is_original else short_hash(output_naming_hash)
    status_monitor.update_status(variant_id, "Compilando Kinematics")

    exe_prefix = config.get("exe_prefix", "inversek2j_")
    executables_dir = config["executables_dir"]
    optimization = config.get("optimization_level", "-O")

    # Nomes dos arquivos objeto
    main_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}_main.o")
    kernel_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}_kernel.o")
    exe_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}")

    include_flags = ["-I", config["include_dir"], "-I", config["input_dir"]]

    # 1. Compilar Main (inversek2j.cpp)
    compile_main_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags,
        "-c", main_cpp, "-o", main_obj_file, "-lm"
    ]
    try:
        subprocess.run(compile_main_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro compilação Main: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Compilação Main")
        return False, None

    # 2. Compilar Kernel (kinematics.cpp variante)
    compile_kernel_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags,
        "-c", kernel_cpp, "-o", kernel_obj_file, "-lm"
    ]
    try:
        subprocess.run(compile_kernel_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro compilação Kernel: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Compilação Kernel")
        return False, None

    # 3. Linkar
    link_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc",
        main_obj_file, kernel_obj_file, "-o", exe_file, "-lm"
    ]
    try:
        result = subprocess.run(link_cmd, check=True, capture_output=True, text=True)
        logging.info(f"[Variante {variant_id}] Linkado com sucesso -> {os.path.basename(exe_file)}")
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro linkagem: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Linkagem")
        return False, None

    os.chmod(exe_file, 0o755)
    status_monitor.update_status(variant_id, "Compilado")
    return True, exe_file

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Executa apenas a parte de profiling (Prof5Fake)."""
    config = {**base_config, **KINEMATICS_CONFIG}
    
    exe_file = resume_context["exe_file"]
    spike_log_file = resume_context["spike_log_file"]
    variant_id = resume_context["variant_id"]
    current_hash = resume_context["variant_hash"]
    prof5_time_file = resume_context["prof5_time_file"]
    prof5_report_path = resume_context["prof5_report_path"]
    
    try:
        status_monitor.update_status(variant_id, "Iniciando Profiling")

        prof5_time = run_prof5_fake(
            spike_log_file, config["prof5_model"], prof5_time_file, prof5_report_path,
            variant_id, status_monitor
        )
        if prof5_time is None:
            return False
            
        try:
            variant_filepath = resume_context["variant_filepath"]
            original_filepath = config["kinematics_source_file"]
            
            with open(variant_filepath, 'r') as f_variant, open(original_filepath, 'r') as f_original:
                variant_lines = f_variant.readlines()
                original_lines = f_original.readlines()

            modified_indices = [i for i, (line1, line2) in enumerate(zip(original_lines, variant_lines)) if line1 != line2]
            
            save_modified_lines_txt(modified_indices, current_hash, config)
        except Exception as e:
            logging.error(f"[Variante {variant_id}] Falha ao salvar índices: {e}")

        logging.info(f"[Variante {variant_id}] Simulação KINEMATICS completa!")
        status_monitor.update_status(variant_id, "Concluída")
        return True
    finally:
        cleanup_variant_files(current_hash, config)

def simulate_variant(current_kernel_filepath, current_hash, base_config, status_monitor, only_spike=False):
    """Simula uma combinação de Kinematics (Main fixo + Kernel variável)."""
    config = {**base_config, **KINEMATICS_CONFIG}
    
    is_original = (os.path.abspath(current_kernel_filepath) == os.path.abspath(config["kinematics_source_file"]))
    variant_id = "original" if is_original else short_hash(current_hash)

    exe_prefix = config.get("exe_prefix", "inversek2j_")
    outputs_dir = config["outputs_dir"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    prof5_results_dir = config["prof5_results_dir"]

    spike_output_file = os.path.join(outputs_dir, f"{exe_prefix}{current_hash}{config['output_suffix']}")
    time_file = os.path.join(outputs_dir, f"{exe_prefix}{current_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(outputs_dir, f"{exe_prefix}{current_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{current_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{current_hash}.txt")
    prof5_report_path = os.path.join(prof5_results_dir, f"prof5_results_{current_hash}.json")

    main_to_compile = config["inversek2j_main_file"]
    kernel_to_compile = current_kernel_filepath 
    
    compiled_ok, exe_file = compile_kinematics_variant(
        main_to_compile, 
        kernel_to_compile, 
        current_hash, 
        config, 
        status_monitor
    )
    if not compiled_ok: 
        return (None, None) if only_spike else False

    if not generate_dump(exe_file, dump_file, variant_id, status_monitor): 
        return (None, None) if only_spike else False

    sim_time = run_spike_simulation(
        exe_file, config["train_data_input"], spike_output_file,
        spike_log_file, variant_id, status_monitor
    )
    if sim_time is None: 
        return (None, None) if only_spike else False
    
    with open(time_file, 'w') as tf: 
        tf.write(f"{sim_time}\n")
    os.chmod(time_file, 0o666)

    resume_context = {
        "exe_file": exe_file,
        "spike_log_file": spike_log_file,
        "dump_file": dump_file,
        "variant_id": variant_id,
        "variant_filepath": current_kernel_filepath,
        "variant_hash": current_hash,
        "prof5_time_file": prof5_time_file,
        "prof5_report_path": prof5_report_path,
    }

    if only_spike:
        return spike_output_file, resume_context

    success = run_profiling_stage(resume_context, base_config, status_monitor)
    
    if success:
        return spike_output_file, None 
    else:
        return None, None

def save_modified_lines_txt(node_modified_lines, variant_hash, config):
    """Salva os índices das linhas modificadas."""
    try:
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
        logging.error(f"Erro ao salvar índices das linhas modificadas: {e}")
        return None

def calculate_custom_error(reference_file, variant_file):
    """
    Calcula o MRE (Mean Relative Error) para KINEMATICS/INVERSEK2J.
    
    A saída consiste em coordenadas/ângulos float.
    O MRE mede a média das diferenças relativas percentuais.
    """
    try:
        def read_floats(filepath):
            with open(filepath, 'r') as f:
                # Lê todo o conteúdo, remove quebras de linha e converte para float
                content = f.read().replace('\n', ' ').split()
                return [float(x) for x in content if x.strip()]

        ref_data = read_floats(reference_file)
        var_data = read_floats(variant_file)

        total_points = len(ref_data)
        
        if total_points == 0:
            logging.error("[INVERSEK2J Error] Arquivo de referência vazio.")
            return float('inf') # Retorna erro infinito se não houver dados

        # Truncagem caso tamanhos sejam diferentes (evita crash)
        if len(ref_data) != len(var_data):
            logging.warning(f"[INVERSEK2J Warning] Tamanhos diferem: Ref={len(ref_data)}, Var={len(var_data)}. Truncando comparação.")
            min_len = min(len(ref_data), len(var_data))
            ref_data = ref_data[:min_len]
            var_data = var_data[:min_len]
            total_points = min_len

        # Cálculo do MRE (Mean Relative Error)
        sum_relative_error = 0.0
        epsilon = 1e-10  # Valor pequeno para evitar divisão por zero
        
        for r, v in zip(ref_data, var_data):
            # Se o valor de referência for 0, usamos o epsilon para não quebrar o cálculo
            denominator = abs(r) if abs(r) > epsilon else epsilon
            
            # |Ref - Var| / |Ref|
            relative_error = abs(r - v) / denominator
            sum_relative_error += relative_error

        mre = sum_relative_error / total_points
        
        logging.info(f"[INVERSEK2J Metric] MRE: {mre:.8f}")
        return mre

    except ValueError as e:
        logging.error(f"[INVERSEK2J Error] Erro de formatação numérica nos arquivos de saída: {e}")
        return None
    except Exception as e:
        logging.error(f"[INVERSEK2J Error] Falha ao calcular MRE: {e}")
        return None