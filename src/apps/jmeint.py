import os
import glob
import logging
import subprocess
import json
import sys

from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação JMEINT
JMEINT_CONFIG = {
    # Arquivos específicos da aplicação
    "jmeint_main_file": "data/applications/jmeint/src/jmeint.cpp",
    "tritri_source_file": "data/applications/jmeint/src/tritri.cpp",
    "train_data_input": "data/applications/jmeint/train.data/input/jmeint_10k.data",

    # Padrões de arquivos para variantes de tritri.cpp
    "source_pattern": "tritri_*.cpp",
    "exe_prefix": "jmeint_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",

    # Parâmetros de geração de variantes
    "input_file_for_variants": "data/applications/jmeint/src/tritri.cpp",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},

    # Parâmetros específicos de compilação
    "include_dir": "data/applications/jmeint/src",
    "optimization_level": "-O",
}

def cleanup_variant_files(variant_hash, config):
    """Remove arquivos temporários de uma variante (log e dump)."""
    exe_prefix = config["exe_prefix"]
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
    """Executa o prof5fake (substituto do Prof5) para análise de energia e performance."""
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
    config = {**base_config, **JMEINT_CONFIG}
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
    """Gera um único arquivo de variante para tritri.cpp."""
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
    """Prepara o ambiente específico para a aplicação JMEINT"""
    config = {**base_config, **JMEINT_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    """Gera variantes específicas para tritri.cpp dentro do contexto de JMEINT"""
    
    config = {**base_config, **JMEINT_CONFIG}
    print(f"DEBUG: Gerando em: {config['input_dir']}")
    
    try:
        # Usar caminhos ABSOLUTOS
        input_path = os.path.abspath(config["input_file_for_variants"])
        output_path = os.path.abspath(config["input_dir"])
        executados_path = os.path.abspath(config["executed_variants_file"])
        
        # --- CORREÇÃO: Adicionada flag --strategy all para combinações ---
        cmd = [
            sys.executable, "src/gera_variantes.py",
            "--input", input_path,
            "--output", output_path,
            "--executados", executados_path,
            "--strategy", "all"  # Força geração combinatória
        ]
        
        print(f"DEBUG: Executando: {' '.join(cmd)}")

        current_script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_script_dir, "..", ".."))
        
        # Timeout aumentado para garantir geração de todas as combinações
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=600)
        
        if result.stdout:
            print(f"DEBUG: STDOUT: {result.stdout[:500]}...")
        if result.stderr:
            print(f"DEBUG: STDERR: {result.stderr}")
        
        if result.returncode == 0:
            import glob
            pattern = os.path.join(config["input_dir"], "tritri_*.cpp")
            generated_files = glob.glob(pattern)
            print(f"DEBUG: {len(generated_files)} variantes encontradas no workspace.")
            return len(generated_files) > 0
        else:
            print(f"ERRO: Comando falhou com código {result.returncode}")
            return False
            
    except Exception as e:
        print(f"ERRO na geração: {e}")
        return False

def find_variants_to_simulate(base_config):
    """Identifica as combinações de JMEINT que precisam ser simuladas."""
    config = {**base_config, **JMEINT_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []

    # Mapa lógico do arquivo original
    tritri_original_path = config["tritri_source_file"]
    with open(tritri_original_path, "r") as f:
        tritri_original_lines = f.readlines()
    _, __, tritri_original_physical_to_logical = parse_code(tritri_original_path)
    
    tritri_original_hash = gerar_hash_codigo_logico(tritri_original_lines, tritri_original_physical_to_logical)

    # Adicionar a versão original
    if tritri_original_hash not in executed_variants:
        variants_to_simulate.append((tritri_original_path, tritri_original_hash))
        logging.info(f"Versão original de JMEINT será simulada (hash: {short_hash(tritri_original_hash)})")

    # Buscar por variantes
    variant_pattern = os.path.join(config["input_dir"], config["source_pattern"])
    variant_files = glob.glob(variant_pattern)
    
    for variant_tritri_file_path in variant_files:
        if os.path.abspath(variant_tritri_file_path) == os.path.abspath(tritri_original_path):
            continue

        with open(variant_tritri_file_path, "r") as f:
            variant_lines = f.readlines()
        
        variant_tritri_hash = gerar_hash_codigo_logico(variant_lines, tritri_original_physical_to_logical)
        
        if variant_tritri_hash not in executed_variants:
            variants_to_simulate.append((variant_tritri_file_path, variant_tritri_hash))
            
    return variants_to_simulate, tritri_original_physical_to_logical

def compile_jmeint_variant(jmeint_cpp_to_compile, tritri_cpp_to_compile, output_naming_hash, config, status_monitor):
    """Compilação especializada: jmeint.cpp (fixo) + tritri.cpp (variável)."""
    
    is_tritri_original = (os.path.abspath(tritri_cpp_to_compile) == os.path.abspath(config["tritri_source_file"]))
    variant_id = "original" if is_tritri_original else short_hash(output_naming_hash)
    status_monitor.update_status(variant_id, "Compilando JMEINT")

    exe_prefix = config.get("exe_prefix", "jmeint_")
    executables_dir = config["executables_dir"]
    optimization = config.get("optimization_level", "-O")

    jmeint_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}_jmeint.o")
    tritri_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}_tritri.o")
    exe_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}")

    include_flags = ["-I", config["include_dir"], "-I", config["input_dir"]]

    # Compilar jmeint.cpp
    compile_jmeint_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags,
        "-c", jmeint_cpp_to_compile, "-o", jmeint_obj_file, "-lm"
    ]
    try:
        subprocess.run(compile_jmeint_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro compilação jmeint: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Compilação (jmeint.cpp)")
        return False, None

    # Compilar tritri.cpp
    compile_tritri_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags,
        "-c", tritri_cpp_to_compile, "-o", tritri_obj_file, "-lm"
    ]
    try:
        subprocess.run(compile_tritri_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro compilação tritri: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, f"Erro Compilação (tritri)")
        return False, None

    # Linkar
    link_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc",
        jmeint_obj_file, tritri_obj_file, "-o", exe_file, "-lm"
    ]
    try:
        subprocess.run(link_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro linkagem: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Linkagem JMEINT")
        return False, None

    os.chmod(exe_file, 0o755)
    return True, exe_file

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Executa apenas a parte de profiling (Prof5Fake)."""
    config = {**base_config, **JMEINT_CONFIG}
    
    spike_log_file = resume_context["spike_log_file"]
    variant_id = resume_context["variant_id"]
    current_tritri_hash = resume_context["tritri_hash"]
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
            variant_filepath = resume_context["tritri_filepath"]
            original_filepath = config["tritri_source_file"]
            
            with open(variant_filepath, 'r') as f_variant, open(original_filepath, 'r') as f_original:
                variant_lines = f_variant.readlines()
                original_lines = f_original.readlines()

            modified_indices = [i for i, (line1, line2) in enumerate(zip(original_lines, variant_lines)) if line1 != line2]
            save_modified_lines_txt(modified_indices, current_tritri_hash, config)
        except Exception:
            pass

        status_monitor.update_status(variant_id, "Concluída JMEINT")
        return True
    finally:
        cleanup_variant_files(current_tritri_hash, config)

def simulate_variant(current_tritri_filepath, current_tritri_hash, base_config, status_monitor, only_spike=False):
    """Simula uma combinação de JMEINT."""
    config = {**base_config, **JMEINT_CONFIG}
    
    is_tritri_original = (os.path.abspath(current_tritri_filepath) == os.path.abspath(config["tritri_source_file"]))
    variant_id = "original" if is_tritri_original else short_hash(current_tritri_hash)

    exe_prefix = config["exe_prefix"]
    outputs_dir = config["outputs_dir"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    prof5_results_dir = config["prof5_results_dir"]

    spike_output_file = os.path.join(outputs_dir, f"{exe_prefix}{current_tritri_hash}{config['output_suffix']}")
    time_file = os.path.join(outputs_dir, f"{exe_prefix}{current_tritri_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(outputs_dir, f"{exe_prefix}{current_tritri_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{current_tritri_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{current_tritri_hash}.txt")
    prof5_report_path = os.path.join(prof5_results_dir, f"prof5_results_{current_tritri_hash}.json")

    jmeint_to_compile = config["jmeint_main_file"]
    tritri_to_compile = current_tritri_filepath 
    output_naming_hash = current_tritri_hash

    compiled_ok, exe_file = compile_jmeint_variant(
        jmeint_to_compile, tritri_to_compile, output_naming_hash, config, status_monitor
    )
    if not compiled_ok: return (None, None) if only_spike else False

    if not generate_dump(exe_file, dump_file, variant_id, status_monitor): 
        return (None, None) if only_spike else False

    sim_time = run_spike_simulation(
        exe_file, config["train_data_input"], spike_output_file,
        spike_log_file, variant_id, status_monitor
    )
    if sim_time is None: return (None, None) if only_spike else False
    
    with open(time_file, 'w') as tf: tf.write(f"{sim_time}\n")
    os.chmod(time_file, 0o666)

    resume_context = {
        "exe_file": exe_file, "spike_log_file": spike_log_file, "dump_file": dump_file,
        "variant_id": variant_id, "tritri_filepath": current_tritri_filepath,
        "tritri_hash": current_tritri_hash, "prof5_time_file": prof5_time_file,
        "prof5_report_path": prof5_report_path,
    }

    if only_spike: return spike_output_file, resume_context
    return spike_output_file, None if run_profiling_stage(resume_context, base_config, status_monitor) else None

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
        return txt_filepath
    except Exception as e:
        logging.error(f"Erro ao salvar linhas modificadas: {e}")
        return None
    
def calculate_custom_error(reference_file, variant_file):
    """Calcula o Miss Rate (Taxa de Erro) para JMEINT."""
    try:
        with open(reference_file, 'r') as f_ref:
            ref_data = [int(x) for x in f_ref.read().split()]
            
        with open(variant_file, 'r') as f_var:
            var_data = [int(x) for x in f_var.read().split()]

        total_points = len(ref_data)
        if total_points == 0: return 1.0 

        if len(ref_data) != len(var_data):
            min_len = min(len(ref_data), len(var_data))
            ref_data = ref_data[:min_len]
            var_data = var_data[:min_len]
            total_points = min_len

        mismatches = sum(1 for r, v in zip(ref_data, var_data) if r != v)
        miss_rate = mismatches / total_points
        logging.info(f"[JMEINT Metric] Miss Rate: {miss_rate:.6f}")
        return miss_rate

    except Exception as e:
        logging.error(f"[JMEINT Error] Falha ao calcular Miss Rate: {e}")
        return None