import os
import glob
import logging
import subprocess
import json
import math
import re

from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação BLACKSCHOLES
BLACKSCHOLES_CONFIG = {
    # Caminhos ajustados conforme seu upload
    "original_file": "data/applications/blackscholes/src/blackscholes.c", 
    "train_data_input": "data/applications/blackscholes/train.data/input/blackscholesTrain_500.data",

    "source_pattern": "blackscholes_*.c", 
    "exe_prefix": "blackscholes_",
    "output_suffix": ".data", 
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",

    "input_file_for_variants": "data/applications/blackscholes/src/blackscholes.c",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX', '/': 'FDIVX'},

    "include_dir": "data/applications/blackscholes/src",
    "optimization_level": "-O", # O3 é recomendado para Blackscholes
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
            except OSError:
                pass

def run_prof5_fake(spike_log_file, prof5_model, prof5_time_file, prof5_report_path, variant_id, status_monitor):
    """Executa o prof5fake para análise de energia e performance."""
    try:
        status_monitor.update_status(variant_id, "Executando Prof5Fake")
        
        if not os.path.exists(spike_log_file):
            logging.error(f"[Variante {variant_id}] Log do Spike não encontrado.")
            return None
        
        # Otimização: Verificar tamanho do log antes de processar
        log_size = os.path.getsize(spike_log_file)
        if log_size > 500 * 1024 * 1024: # 500MB
             logging.warning(f"[Variante {variant_id}] Log muito grande ({log_size/1024/1024:.1f}MB). Pode demorar.")

        instrucoes_dict = contar_instrucoes_log(spike_log_file)
        if not instrucoes_dict:
            return None
            
        resultados_energia = avaliar_modelo_energia(instrucoes_dict, prof5_model)
        if not resultados_energia:
            return None
            
        with open(prof5_report_path, 'w') as f:
            json.dump(resultados_energia, f, indent=2)
        os.chmod(prof5_report_path, 0o666)
        
        latency_ms = resultados_energia["summary"]["latency_ms"]
        
        with open(prof5_time_file, 'w') as f:
            f.write(f"{latency_ms}\n")
        os.chmod(prof5_time_file, 0o666)
        
        status_monitor.update_status(variant_id, "Prof5Fake Concluído")
        return latency_ms
        
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5Fake: {e}")
        return None

def get_pruning_config(base_config):
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
    modified_lines_content = list(original_lines)
    for idx in modified_line_indices:
        modified_lines_content[idx] = apply_transformation(modified_lines_content[idx], config["operations_map"])

    variant_hash = gerar_hash_codigo_logico(modified_lines_content, physical_to_logical)
    
    variant_dir = config.get("input_dir", "storage/variantes")
    # Garante que a pasta existe
    os.makedirs(variant_dir, exist_ok=True)
    
    base_name = "blackscholes"
    variant_filepath = os.path.join(variant_dir, f"{base_name}_{variant_hash}.c")

    with open(variant_filepath, 'w') as f:
        f.writelines(modified_lines_content)
        
    return variant_filepath, variant_hash

def prepare_environment(base_config):
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    # Garante criação do diretório de variantes antes de copiar
    os.makedirs(config["input_dir"], exist_ok=True)
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    import subprocess
    
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    logging.info(f"Gerando variantes em: {config['input_dir']}")
    
    try:
        # Resolve caminhos absolutos
        current_script_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_script_dir, "..", ".."))
        
        input_path = os.path.abspath(os.path.join(project_root, config["input_file_for_variants"]))
        output_path = os.path.abspath(config["input_dir"])
        executados_path = os.path.abspath(config["executed_variants_file"])
        
        cmd = [
            "python3", "src/gera_variantes.py",
            "--input", input_path,
            "--output", output_path,
            "--executados", executados_path
        ]
        
        logging.info(f"Executando gerador: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
        
        if result.returncode == 0:
            pattern = os.path.join(config["input_dir"], "blackscholes_*.c")
            generated_files = glob.glob(pattern)
            logging.info(f"{len(generated_files)} variantes disponíveis no workspace.")
            return True
        else:
            logging.error(f"Erro na geração. Código: {result.returncode}. STDERR: {result.stderr}")
            return False
            
    except Exception as e:
        logging.error(f"Exceção na geração: {e}")
        return False

def find_variants_to_simulate(base_config):
    """
    OTIMIZADA: Evita parsear arquivos se o hash estiver no nome do arquivo.
    """
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []

    # Processa o Original
    original_path = config["original_file"]
    try:
        with open(original_path, "r") as f:
            original_lines = f.readlines()
        _, __, physical_to_logical = parse_code(original_path)
        original_hash = gerar_hash_codigo_logico(original_lines, physical_to_logical)
        
        if original_hash not in executed_variants:
            variants_to_simulate.append((original_path, original_hash))
    except FileNotFoundError:
        logging.error(f"Arquivo original não encontrado: {original_path}")
        return [], None

    # Busca variantes
    variant_pattern = os.path.join(config["input_dir"], config["source_pattern"])
    variant_files = glob.glob(variant_pattern)
    logging.info(f"Analisando {len(variant_files)} arquivos de variante...")
    
    # Regex para extrair hash do nome do arquivo: blackscholes_HASH.c
    hash_pattern = re.compile(r"blackscholes_([a-fA-F0-9]+)\.c$")

    for variant_file_path in variant_files:
        filename = os.path.basename(variant_file_path)
        
        # Ignora se for o próprio arquivo original copiado
        if os.path.abspath(variant_file_path) == os.path.abspath(original_path):
            continue

        # Tenta extrair o hash do nome do arquivo (MUITO MAIS RÁPIDO)
        match = hash_pattern.search(filename)
        
        if match:
            variant_hash = match.group(1)
            # Verifica se já executou antes de gastar tempo abrindo arquivo
            if variant_hash not in executed_variants:
                variants_to_simulate.append((variant_file_path, variant_hash))
        else:
            # Fallback: Se o nome não tiver o hash, calcula do jeito lento
            with open(variant_file_path, "r") as f:
                variant_lines = f.readlines()
            variant_hash = gerar_hash_codigo_logico(variant_lines, physical_to_logical)
            if variant_hash not in executed_variants:
                variants_to_simulate.append((variant_file_path, variant_hash))
            
    logging.info(f"Total de novas variantes para simular: {len(variants_to_simulate)}")
    return variants_to_simulate, physical_to_logical

def compile_blackscholes_variant(source_to_compile, variant_hash, config, status_monitor):
    is_original = (os.path.abspath(source_to_compile) == os.path.abspath(config["original_file"]))
    variant_id = "original" if is_original else short_hash(variant_hash)
    status_monitor.update_status(variant_id, "Compilando")

    exe_prefix = config.get("exe_prefix", "blackscholes_")
    executables_dir = config["executables_dir"]
    optimization = config.get("optimization_level", "-O3")

    exe_file = os.path.join(executables_dir, f"{exe_prefix}{variant_hash}")
    include_flags = ["-I", config["include_dir"], "-I", config["input_dir"]]

    compile_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags,
        source_to_compile, "-o", exe_file, "-lm"
    ]
    
    try:
        subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        os.chmod(exe_file, 0o755)
        status_monitor.update_status(variant_id, "Compilado")
        return True, exe_file
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro compilação: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Compilação")
        return False, None

def run_profiling_stage(resume_context, base_config, status_monitor):
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    
    exe_file = resume_context["exe_file"]
    spike_log_file = resume_context["spike_log_file"]
    variant_id = resume_context["variant_id"]
    current_hash = resume_context["variant_hash"]
    prof5_time_file = resume_context["prof5_time_file"]
    prof5_report_path = resume_context["prof5_report_path"]
    
    try:
        prof5_time = run_prof5_fake(
            spike_log_file, config["prof5_model"], prof5_time_file, prof5_report_path,
            variant_id, status_monitor
        )
        if prof5_time is None:
            return False
            
        try:
            variant_filepath = resume_context["variant_filepath"]
            original_filepath = config["original_file"]
            # Apenas salva se não for o original
            if variant_id != "original":
                with open(variant_filepath, 'r') as f_v, open(original_filepath, 'r') as f_o:
                    v_lines = f_v.readlines()
                    o_lines = f_o.readlines()
                mod_idx = [i for i, (a, b) in enumerate(zip(o_lines, v_lines)) if a != b]
                save_modified_lines_txt(mod_idx, current_hash, config)
        except Exception as e:
            logging.warning(f"Erro ao salvar linhas modificadas: {e}")

        status_monitor.update_status(variant_id, "Concluída")
        return True
    finally:
        cleanup_variant_files(current_hash, config)

def simulate_variant(source_filepath, variant_hash, base_config, status_monitor, only_spike=False):
    config = {**base_config, **BLACKSCHOLES_CONFIG}
    
    is_original = (os.path.abspath(source_filepath) == os.path.abspath(config["original_file"]))
    variant_id = "original" if is_original else short_hash(variant_hash)

    # Prepara caminhos de saída
    exe_prefix = config["exe_prefix"]
    outputs_dir = config["outputs_dir"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    prof5_results_dir = config["prof5_results_dir"]

    spike_output_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{config['output_suffix']}")
    time_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    prof5_report_path = os.path.join(prof5_results_dir, f"prof5_results_{variant_hash}.json")

    # 1. Compilar
    compiled_ok, exe_file = compile_blackscholes_variant(source_filepath, variant_hash, config, status_monitor)
    if not compiled_ok: return (None, None) if only_spike else False

    # 2. Dump (opcional, mas bom para debug)
    generate_dump(exe_file, dump_file, variant_id, status_monitor)

    # 3. Simulação Spike
    # IMPORTANTE: run_spike_simulation deve ter um TIMEOUT interno para evitar loops infinitos
    sim_time = run_spike_simulation(
        exe_file, config["train_data_input"], spike_output_file,
        spike_log_file, variant_id, status_monitor
    )
    
    if sim_time is None: 
        logging.warning(f"[Variante {variant_id}] Simulação falhou (possível loop infinito ou crash).")
        return (None, None) if only_spike else False
    
    with open(time_file, 'w') as tf: tf.write(f"{sim_time}\n")
    os.chmod(time_file, 0o666)

    resume_context = {
        "exe_file": exe_file,
        "spike_log_file": spike_log_file,
        "dump_file": dump_file,
        "variant_id": variant_id,
        "variant_filepath": source_filepath,
        "variant_hash": variant_hash,
        "prof5_time_file": prof5_time_file,
        "prof5_report_path": prof5_report_path,
    }

    if only_spike:
        return spike_output_file, resume_context

    # 4. Profiling
    success = run_profiling_stage(resume_context, base_config, status_monitor)
    return (spike_output_file, None) if success else (None, None)

def save_modified_lines_txt(node_modified_lines, variant_hash, config):
    try:
        linhas_dir = config.get("linhas_modificadas_dir", "storage/linhas_modificadas")
        txt_filepath = os.path.join(linhas_dir, f"linhas_{variant_hash}.txt")
        with open(txt_filepath, 'w') as f:
            for idx in node_modified_lines: f.write(f"{idx}\n")
        os.chmod(txt_filepath, 0o666)
        return txt_filepath
    except Exception:
        return None
    
def calculate_custom_error(reference_file, variant_file):
    """
    Calcula MRE (Mean Relative Error) de forma segura para memória.
    Lê os arquivos linha por linha (streaming) para evitar carregar GBs se houver loop.
    """
    try:
        def read_floats(filepath):
            """Gerador que lê floats um por um para economizar memória"""
            with open(filepath, 'r') as f:
                for line in f:
                    # Proteção contra linhas gigantescas
                    if len(line) > 1024: continue 
                    for part in line.split():
                        try:
                            yield float(part)
                        except ValueError:
                            pass

        ref_gen = read_floats(reference_file)
        var_gen = read_floats(variant_file)
        
        sum_relative_error = 0.0
        count = 0
        MAX_POINTS = 1000000 # Segurança: Analisa no máximo 1 milhão de pontos

        while count < MAX_POINTS:
            try:
                r = next(ref_gen)
                v = next(var_gen)
            except StopIteration:
                break # Fim de um dos arquivos

            if r == 0:
                if v != 0: sum_relative_error += 1.0
            else:
                sum_relative_error += abs((r - v) / r)
            
            count += 1
        
        if count == 0:
            logging.error("[MRE] Arquivo de saída vazio.")
            return 1.0

        mre = sum_relative_error / count
        logging.info(f"[MRE Metric] Error: {mre:.6f} (amostra de {count} pontos)")
        return mre

    except Exception as e:
        logging.error(f"[MRE Error] Falha: {e}")
        return None