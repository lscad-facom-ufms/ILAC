import os
import glob
import logging
import json
import subprocess
import shutil
import sys
import re
import numpy as np

from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, get_modified_logical_lines
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação Kmeans
KMEANS_CONFIG = {
    "original_file": "data/applications/kmeans/src/distance.c",
    "kmeans_file": "data/applications/kmeans/src/kmeans.c",
    "distance_file": "data/applications/kmeans/src/distance.c",
    "rgbimage_file": "data/applications/kmeans/src/rgbimage.c",
    "segmentation_file": "data/applications/kmeans/src/segmentation.c",
    "train_data_input": "data/applications/kmeans/train.data/input/1.rgb",
    "source_pattern": "distance_*.c",
    "exe_prefix": "kmeans_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    "prof5_model": "data/models/APPROX_1.json", # Crítico para o profiling
    "input_file_for_variants": "data/applications/kmeans/src/distance.c",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    "include_dir": "data/applications/kmeans/src",
    "optimization_level": "-O",
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
                pass

def run_prof5_fake(spike_log_file, prof5_model, prof5_time_file, prof5_report_path, variant_id, status_monitor):
    try:
        status_monitor.update_status(variant_id, "Executando Prof5Fake")
        if not os.path.exists(spike_log_file): return None
            
        instrucoes_dict = contar_instrucoes_log(spike_log_file)
        if not instrucoes_dict: return None
            
        if not prof5_model or not os.path.exists(prof5_model):
            prof5_model = "data/profiles/riscv_energy_model.json"
            if not os.path.exists(prof5_model): return None

        resultados_energia = avaliar_modelo_energia(instrucoes_dict, prof5_model)
        if not resultados_energia: return None
            
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
    config = {**base_config, **KMEANS_CONFIG}
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
    from transformations import apply_transformation
    modified_lines_content = list(original_lines)
    for idx in modified_line_indices:
        orig = modified_lines_content[idx]
        transformed = apply_transformation(orig, config["operations_map"])
        if not transformed.endswith("\n") and orig.endswith("\n"):
            transformed += "\n"
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
    config = {**base_config, **KMEANS_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    os.makedirs(config["input_dir"], exist_ok=True)
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    config = {**base_config, **KMEANS_CONFIG}
    output_dir = os.path.abspath(config.get("input_dir", "storage/variantes"))
    input_path = os.path.abspath(config["input_file_for_variants"])
    executados = os.path.abspath(config.get("executed_variants_file", ""))
    os.makedirs(output_dir, exist_ok=True)

    cmd = [sys.executable, "src/gera_variantes.py", "--input", input_path, "--output", output_dir]
    
    # Lógica Inteligente: Suporte a combinações se não for força bruta
    search_mode = config.get("search_mode", "exhaustive").lower()
    if search_mode not in ["exhaustive", "forcabruta", "forca_bruta"]:
        cmd.extend(["--strategy", "all"])
    
    if executados and os.path.exists(executados):
        cmd.extend(["--executados", executados])

    try:
        env = os.environ.copy()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

        subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=1800, env=env)
        pattern = os.path.join(output_dir, config["source_pattern"])
        return len(glob.glob(pattern)) > 0
    except Exception as e:
        logging.error(f"Erro ao gerar variantes: {e}")
        return False

def find_variants_to_simulate(base_config):
    config = {**base_config, **KMEANS_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []

    with open(config["distance_file"], "r") as f:
        original_lines = f.readlines()
    _, __, original_physical_to_logical = parse_code(config["distance_file"])
    original_hash = gerar_hash_codigo_logico(original_lines, original_physical_to_logical)
    
    if original_hash not in executed_variants:
        variants_to_simulate.append((config["distance_file"], original_hash))

    pattern = os.path.join(config["input_dir"], config["source_pattern"])
    if not glob.glob(pattern):
        generate_variants(base_config)

    for file in glob.glob(pattern):
        with open(file, "r") as f:
            variant_lines = f.readlines()
        variant_hash = gerar_hash_codigo_logico(variant_lines, original_physical_to_logical)
        if variant_hash not in executed_variants:
            variants_to_simulate.append((file, variant_hash))
            
    return variants_to_simulate, original_physical_to_logical

def compile_kmeans_variant(variant_distance_file, variant_hash, config, status_monitor):
    variant_id = "original" if variant_distance_file == config["distance_file"] else short_hash(variant_hash)
    status_monitor.update_status(variant_id, "Compilando KMEANS")
    
    exe_prefix = config.get("exe_prefix", "kmeans_")
    exe_file = os.path.join(config["executables_dir"], f"{exe_prefix}{variant_hash}")
    include_flags = ["-I", config["input_dir"], "-I", config.get("include_dir", "include")]
    
    compile_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdcv", config.get("optimization_level", "-O"), 
        *include_flags, config["kmeans_file"], variant_distance_file,
        config["rgbimage_file"], config["segmentation_file"], "-o", exe_file, "-lm"
    ]
    try:
        subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        os.chmod(exe_file, 0o755)
        return True, exe_file
    except subprocess.CalledProcessError:
        status_monitor.update_status(variant_id, "Erro Compilação KMEANS")
        return False, None

def run_profiling_stage(resume_context, base_config, status_monitor):
    config = {**base_config, **KMEANS_CONFIG}
    try:
        prof5_time = run_prof5_fake(
            resume_context["spike_log_file"], config.get("prof5_model"),
            resume_context["prof5_time_file"], resume_context["prof5_report_path"],
            resume_context["variant_id"], status_monitor
        )
        if prof5_time is None: return False
        
        try:
            save_modified_lines_txt(resume_context["variant_file"], config["original_file"], resume_context["variant_hash"], config)
        except Exception: pass

        status_monitor.update_status(resume_context["variant_id"], "Concluída")
        return True
    finally:
        cleanup_variant_files(resume_context["variant_hash"], config)

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    config = {**KMEANS_CONFIG, **base_config}
    variant_id = "original" if variant_file == config["original_file"] else short_hash(variant_hash)
    
    exe_prefix = config["exe_prefix"]
    output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}.rgb")
    time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(config["dump_dir"], f"dump_{variant_hash}.txt")
    prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
    
    compiled_ok, exe_file = compile_kmeans_variant(variant_file, variant_hash, config, status_monitor)
    if not compiled_ok: return (None, None) if only_spike else False
    
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
        return (None, None) if only_spike else False

    input_file = config.get("train_data_input", "axbench/applications/kmeans/train.data/input/1.rgb")
    sim_time = run_spike_simulation(exe_file, input_file, output_file, spike_log_file, variant_id, status_monitor)
    if sim_time is None: return (None, None) if only_spike else False

    with open(time_file, 'w') as tf: tf.write(f"{sim_time}\n")
    
    # Conversão de saída
    csv_output = output_file.replace(".rgb", ".csv")
    try: rgb_to_csv(output_file, csv_output, 512, 512)
    except: pass

    resume_context = {
        "exe_file": exe_file, "spike_log_file": spike_log_file, "dump_file": dump_file,
        "variant_id": variant_id, "variant_file": variant_file, "variant_hash": variant_hash,
        "prof5_time_file": prof5_time_file, "prof5_report_path": prof5_report_path,
    }

    if only_spike: return output_file, resume_context
    return output_file, None if run_profiling_stage(resume_context, base_config, status_monitor) else None

def rgb_to_csv(rgb_path, csv_path, width, height):
    pixel_bytes = width * height * 3
    with open(rgb_path, "rb") as f: data = f.read(pixel_bytes)
    arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))
    with open(csv_path, "w") as f:
        for row in arr:
            for p in row: f.write(f"{p[0]},{p[1]},{p[2]}\n")

def save_modified_lines_txt(variant_file, original_file, variant_hash, config):
    try:
        with open(original_file, 'r') as f_o, open(variant_file, 'r') as f_v:
            o_lines, v_lines = f_o.readlines(), f_v.readlines()
        _, __, p2l = parse_code(original_file)
        logical_indices = get_modified_logical_lines(o_lines, v_lines, p2l)

        linhas_dir = config.get("linhas_modificadas_dir", "storage/linhas_modificadas")
        os.makedirs(linhas_dir, exist_ok=True)
        txt_filepath = os.path.join(linhas_dir, f"linhas_{variant_hash}.txt")
        with open(txt_filepath, 'w') as f:
            for idx in logical_indices: f.write(f"{idx}\n")
        return txt_filepath
    except: return None

def calculate_custom_error(reference_file, variant_file):
    """Calcula MRE baseado no CSV gerado pelo KMEANS."""
    try:
        ref_csv = reference_file.replace('.rgb', '.csv') if reference_file.endswith('.rgb') else reference_file
        var_csv = variant_file.replace('.rgb', '.csv') if variant_file.endswith('.rgb') else variant_file
        
        with open(ref_csv, 'r') as f1, open(var_csv, 'r') as f2:
            r_lines = f1.readlines()
            v_lines = f2.readlines()
            
        sum_err, count = 0.0, 0
        for r_line, v_line in zip(r_lines, v_lines):
            r_vals = [float(x) for x in r_line.strip().split(',')]
            v_vals = [float(x) for x in v_line.strip().split(',')]
            for rv, vv in zip(r_vals, v_vals):
                sum_err += abs((rv - vv) / rv) if rv != 0 else (1.0 if vv != 0 else 0.0)
                count += 1
        return sum_err / count if count > 0 else 1.0
    except Exception as e:
        logging.error(f"Erro ao calcular MRE do Kmeans: {e}")
        return None