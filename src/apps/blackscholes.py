import os
import glob
import logging
import subprocess
import json
import re
import sys

from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file
from execution.compilation import compile_variant, generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação BLACKSCHOLES
BLACKSCHOLES_CONFIG = {
    "original_file": "data/applications/blackscholes/src/blackscholes.c", 
    "train_data_input": "data/applications/blackscholes/src/train.data/input/blackscholesTrain_500.data",
    "source_pattern": "blackscholes_*.c", 
    "exe_prefix": "blackscholes_",
    "output_suffix": ".data", 
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    "prof5_model": "data/models/APPROX_1.json", # Crítico para o profiling
    "input_file_for_variants": "data/applications/blackscholes/src/blackscholes.c",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX', '/': 'FDIVX'},
    "include_dir": "data/applications/blackscholes/src",
    "optimization_level": "-O3", # Otimização recomendada para Blackscholes
}

def cleanup_variant_files(variant_hash, config):
    """Remove arquivos temporários (logs, dumps), mas preserva resultados."""
    exe_prefix = config["exe_prefix"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    
    for f in [spike_log_file, dump_file]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError:
                logging.debug(f"Não foi possível remover {f}")

def run_prof5_fake(spike_log_file, prof5_model, prof5_time_file, prof5_report_path, variant_id, status_monitor):
    """Executa o prof5fake para estimar energia e performance."""
    try:
        status_monitor.update_status(variant_id, "Executando Prof5Fake")
        
        if not os.path.exists(spike_log_file):
            return None
            
        instrucoes_dict = contar_instrucoes_log(spike_log_file)
        if not instrucoes_dict:
            return None
            
        if not prof5_model or not os.path.exists(prof5_model):
            default_model = "data/profiles/riscv_energy_model.json"
            if os.path.exists(default_model):
                prof5_model = default_model
            else:
                return None

        resultados_energia = avaliar_modelo_energia(instrucoes_dict, prof5_model)
        if not resultados_energia:
            return None
            
        os.makedirs(os.path.dirname(prof5_report_path), exist_ok=True)
        with open(prof5_report_path, 'w') as f:
            json.dump(resultados_energia, f, indent=2, sort_keys=True)
        
        latency_ms = resultados_energia["summary"]["latency_ms"]
        with open(prof5_time_file, 'w') as f:
            f.write(f"{latency_ms}\n")
        
        status_monitor.update_status(variant_id, "Prof5Fake Concluído")
        return latency_ms
        
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5Fake: {e}")
        return None

def get_pruning_config(base_config):
    """Retorna a configuração para o modo de árvore de poda e busca gulosa."""
    config = {**base_config, **BLACKSCHOLES_CONFIG}
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
    """Gera o arquivo C da variante com múltiplas modificações aplicadas."""
    modified_lines_content = list(original_lines)
    for idx in modified_line_indices:
        orig = modified_lines_content[idx]
        transformed = apply_transformation(orig, config["operations_map"])
        if not transformed.endswith("\n") and orig.endswith("\n"):
            transformed = transformed + "\n"
        modified_lines_content[idx] = transformed

    variant_hash = gerar_hash_codigo_logico(modified_lines_content, physical_to_logical)
    
    variant_dir = config.get("input_dir", "storage/variantes")
    base_name, ext = os.path.splitext(os.path.basename(config["input_file_for_variants"]))
    variant_filepath = os.path.join(variant_dir, f"{base_name}_{variant_hash}{ext}")

    os.makedirs(variant_dir, exist_ok=True)
    with open(variant_filepath, 'w', encoding='utf-8') as f:
        f.writelines(modified_lines_content)
        
    return variant_filepath, variant_hash

def prepare_environment(base_config):
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    os.makedirs(config["input_dir"], exist_ok=True)
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    """Gera as variantes invocando o script subjacente com estratégia combinatória."""
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    output_dir = os.path.abspath(config.get("input_dir", "storage/variantes"))
    input_path = os.path.abspath(config["input_file_for_variants"])
    executados = os.path.abspath(config.get("executed_variants_file", ""))
    
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        sys.executable, "src/gera_variantes.py",
        "--input", input_path,
        "--output", output_dir,
        "--strategy", "all"  # Flag CRÍTICA para mesclar aproximações
    ]
    
    if executados and os.path.exists(executados):
        cmd += ["--executados", executados]

    logging.info(f"Gerando variantes Blackscholes: {' '.join(cmd)}")
    
    try:
        env = os.environ.copy()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=1800, env=env)
        
        if result.returncode != 0:
            logging.error(f"Erro no subprocesso: {result.stderr}")
            return False
            
        pattern = os.path.join(output_dir, config["source_pattern"])
        return len(glob.glob(pattern)) > 0
            
    except Exception as e:
        logging.error(f"Exceção fatal na geração: {e}")
        return False

def find_variants_to_simulate(base_config):
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []

    _, __, physical_to_logical = parse_code(config["original_file"])
    with open(config["original_file"], "r") as f:
        original_hash = gerar_hash_codigo_logico(f.readlines(), physical_to_logical)
    
    if original_hash not in executed_variants:
        variants_to_simulate.append((config["original_file"], original_hash))

    pattern = os.path.join(config["input_dir"], config["source_pattern"])
    
    # Se não existirem variantes na pasta, dispara a geração automaticamente
    if not glob.glob(pattern):
        generate_variants(base_config)

    hash_pattern = re.compile(r"blackscholes_([a-fA-F0-9]+)\.c$")

    for file_path in glob.glob(pattern):
        match = hash_pattern.search(os.path.basename(file_path))
        v_hash = match.group(1) if match else None
        if v_hash and v_hash not in executed_variants:
            variants_to_simulate.append((file_path, v_hash))
            
    return variants_to_simulate, physical_to_logical

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Etapa de profiling destacada para rodar assincronamente ou em lote."""
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    spike_log_file = resume_context["spike_log_file"]
    variant_id = resume_context["variant_id"]
    prof5_time_file = resume_context["prof5_time_file"]
    prof5_report_path = resume_context["prof5_report_path"]
    variant_hash = resume_context["variant_hash"]
    
    try:
        status_monitor.update_status(variant_id, "Iniciando Profiling")
        prof5_time = run_prof5_fake(
            spike_log_file, config.get("prof5_model"), prof5_time_file, prof5_report_path,
            variant_id, status_monitor
        )
        if prof5_time is None:
            return False
            
        try:
            variant_filepath = resume_context["variant_filepath"]
            save_modified_lines(variant_filepath, config["original_file"], variant_hash, config, parse_code)
        except Exception: pass

        status_monitor.update_status(variant_id, "Concluída")
        return True
    finally:
        cleanup_variant_files(variant_hash, config)

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    """Executa o ciclo suportando 'only_spike' e contexto de continuidade."""
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    variant_id = "original" if "blackscholes.c" in variant_file else short_hash(variant_hash)
    
    exe_file = os.path.join(config["executables_dir"], f"{config['exe_prefix']}{variant_hash}")
    output_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['output_suffix']}")
    time_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['prof5_suffix']}")
    prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
    
    spike_log_file = os.path.join(config["logs_dir"], f"{config['exe_prefix']}{variant_hash}.log")
    dump_file = os.path.join(config["dump_dir"], f"dump_{variant_hash}.txt")
    
    # 1. Compilação
    if not compile_variant(variant_file, variant_hash, config, status_monitor):
        return (None, None) if only_spike else False
    
    # 2. Dump
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
        return (None, None) if only_spike else False
    
    # 3. Simulação Spike
    sim_time = run_spike_simulation(exe_file, config["train_data_input"], output_file, 
                                    spike_log_file, variant_id, status_monitor)
    if sim_time is None: 
        return (None, None) if only_spike else False
    
    with open(time_file, 'w') as tf: 
        tf.write(f"{sim_time}\n")
    
    # Contexto para o Profiler
    resume_context = {
        "exe_file": exe_file, "spike_log_file": spike_log_file, "dump_file": dump_file,
        "variant_id": variant_id, "variant_filepath": variant_file,
        "variant_hash": variant_hash, "prof5_time_file": prof5_time_file,
        "prof5_report_path": prof5_report_path,
    }

    if only_spike:
        return output_file, resume_context
        
    # 4. Profiling Imediato
    success = run_profiling_stage(resume_context, base_config, status_monitor)
    return output_file, None if success else None

def calculate_custom_error(reference_file, variant_file):
    """MRE para Blackscholes usando streaming (Mantido)."""
    try:
        def read_floats(filepath):
            with open(filepath, 'r') as f:
                for line in f:
                    if len(line) > 1024: continue 
                    for part in line.split():
                        try: yield float(part)
                        except ValueError: pass
        ref_gen, var_gen = read_floats(reference_file), read_floats(variant_file)
        sum_err, count = 0.0, 0
        for r in ref_gen:
            try: v = next(var_gen)
            except StopIteration: break
            sum_err += abs((r - v) / r) if r != 0 else (1.0 if v != 0 else 0.0)
            count += 1
            if count >= 1000000: break
        return sum_err / count if count > 0 else 1.0
    except Exception as e:
        logging.error(f"Erro no cálculo de MRE: {e}")
        return None