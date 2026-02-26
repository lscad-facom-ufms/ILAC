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

# Configurações específicas para a aplicação Sobel
SOBEL_CONFIG = {
    "original_file": "data/applications/sobel/src/convolution.cpp",
    "sobel_file": "data/applications/sobel/src/sobel.cpp",
    "convolution_file": "data/applications/sobel/src/convolution.cpp",
    "rgb_image_file": "data/applications/sobel/src/rgb_image.cpp",
    "approx_file": "data/reference/approx.h",
    "source_pattern": "convolution_*.cpp",
    "exe_prefix": "sobel_",
    "output_suffix": ".csv", # Geralmente sobel gera dados ou img. Adaptado aqui se .rgb
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    "prof5_model": "data/models/APPROX_1.json",
    "input_file_for_variants": "data/applications/sobel/src/convolution.cpp",
    "input_file": "data/applications/sobel/train.data/input/32x32.rgb",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    "include_dir": "data/applications/sobel/src",
    "optimization_level": "-O",
}

def cleanup_variant_files(variant_hash, config):
    exe_prefix = config["exe_prefix"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    for f in [spike_log_file, dump_file]:
        if os.path.exists(f):
            try: os.remove(f)
            except: pass

def run_prof5_fake(spike_log_file, prof5_model, prof5_time_file, prof5_report_path, variant_id, status_monitor):
    try:
        status_monitor.update_status(variant_id, "Executando Prof5Fake")
        if not os.path.exists(spike_log_file): return None
        instrucoes = contar_instrucoes_log(spike_log_file)
        if not instrucoes: return None
        if not prof5_model or not os.path.exists(prof5_model):
            prof5_model = "data/profiles/riscv_energy_model.json"
            if not os.path.exists(prof5_model): return None
        resultados = avaliar_modelo_energia(instrucoes, prof5_model)
        if not resultados: return None
        
        os.makedirs(os.path.dirname(prof5_report_path), exist_ok=True)
        with open(prof5_report_path, 'w') as f: json.dump(resultados, f, indent=2)
        tempo = resultados["summary"]["latency_ms"]
        with open(prof5_time_file, 'w') as f: f.write(f"{tempo}\n")
        
        status_monitor.update_status(variant_id, "Prof5Fake Concluído")
        return tempo
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5Fake: {e}")
        return None

def get_pruning_config(base_config):
    config = {**base_config, **SOBEL_CONFIG}
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
        if not transformed.endswith("\n") and orig.endswith("\n"): transformed += "\n"
        modified_lines_content[idx] = transformed

    variant_hash = gerar_hash_codigo_logico(modified_lines_content, physical_to_logical)
    variant_dir = config.get("input_dir", "storage/variantes")
    base_name, ext = os.path.splitext(os.path.basename(config["input_file_for_variants"]))
    variant_filepath = os.path.join(variant_dir, f"{base_name}_{variant_hash}{ext}")

    os.makedirs(variant_dir, exist_ok=True)
    with open(variant_filepath, 'w', encoding='utf-8') as f: f.writelines(modified_lines_content)
    return variant_filepath, variant_hash

def prepare_environment(base_config):
    config = {**base_config, **SOBEL_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    os.makedirs(config["input_dir"], exist_ok=True)
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    config = {**base_config, **SOBEL_CONFIG}
    output_dir = os.path.abspath(config.get("input_dir", "storage/variantes"))
    input_path = os.path.abspath(config["input_file_for_variants"])
    executados = os.path.abspath(config.get("executed_variants_file", ""))
    os.makedirs(output_dir, exist_ok=True)

    cmd = [sys.executable, "src/gera_variantes.py", "--input", input_path, "--output", output_dir]
    
    # Lógica Inteligente para mesclar aproximações se não for força bruta
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
        return False

def find_variants_to_simulate(base_config):
    config = {**base_config, **SOBEL_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []

    with open(config["original_file"], "r") as f: original_lines = f.readlines()
    _, __, p2l = parse_code(config["original_file"])
    original_hash = gerar_hash_codigo_logico(original_lines, p2l)
    
    if original_hash not in executed_variants:
        variants_to_simulate.append((config["original_file"], original_hash))

    pattern = os.path.join(config["input_dir"], config["source_pattern"])
    if not glob.glob(pattern): generate_variants(base_config)

    for file in glob.glob(pattern):
        with open(file, "r") as f: v_lines = f.readlines()
        v_hash = gerar_hash_codigo_logico(v_lines, p2l)
        if v_hash not in executed_variants:
            variants_to_simulate.append((file, v_hash))
            
    return variants_to_simulate, p2l

def compile_sobel_variant(variant_sobel_file, variant_hash, config, status_monitor):
    variant_id = "original" if variant_sobel_file == config["original_file"] else short_hash(variant_hash)
    status_monitor.update_status(variant_id, "Compilando SOBEL")
    exe_file = os.path.join(config["executables_dir"], f"{config['exe_prefix']}{variant_hash}")
    
    include_flags = ["-I" + config["include_dir"], "-I" + config["input_dir"]]
    compile_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdcv", config.get("optimization_level", "-O"), 
        *include_flags, variant_sobel_file, config["rgb_image_file"], config["sobel_file"], 
        "-o", exe_file, "-lm"
    ]
    try:
        subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        os.chmod(exe_file, 0o755)
        return True, exe_file
    except subprocess.CalledProcessError:
        status_monitor.update_status(variant_id, "Erro Compilação SOBEL")
        return False, None

def run_profiling_stage(resume_context, base_config, status_monitor):
    config = {**base_config, **SOBEL_CONFIG}
    try:
        prof5_time = run_prof5_fake(
            resume_context["spike_log_file"], config.get("prof5_model"),
            resume_context["prof5_time_file"], resume_context["prof5_report_path"],
            resume_context["variant_id"], status_monitor
        )
        if prof5_time is None: return False
        try: save_modified_lines_txt(resume_context["variant_file"], config["original_file"], resume_context["variant_hash"], config)
        except: pass
        status_monitor.update_status(resume_context["variant_id"], "Concluída")
        return True
    finally:
        cleanup_variant_files(resume_context["variant_hash"], config)

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    config = {**SOBEL_CONFIG, **base_config}
    variant_id = "original" if variant_file == config["original_file"] else short_hash(variant_hash)
    exe_prefix = config["exe_prefix"]
    
    output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['output_suffix']}")
    time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(config["dump_dir"], f"dump_{variant_hash}.txt")
    prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
    
    compiled_ok, exe_file = compile_sobel_variant(variant_file, variant_hash, config, status_monitor)
    if not compiled_ok: return (None, None) if only_spike else False
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor): return (None, None) if only_spike else False

    input_file = config.get("input_file", "data/applications/sobel/train.data/input/32x32.rgb")
    sim_time = run_spike_simulation(exe_file, input_file, output_file, spike_log_file, variant_id, status_monitor)
    if sim_time is None: return (None, None) if only_spike else False

    with open(time_file, 'w') as tf: tf.write(f"{sim_time}\n")

    resume_context = {
        "exe_file": exe_file, "spike_log_file": spike_log_file, "dump_file": dump_file,
        "variant_id": variant_id, "variant_file": variant_file, "variant_hash": variant_hash,
        "prof5_time_file": prof5_time_file, "prof5_report_path": prof5_report_path,
    }

    if only_spike: return output_file, resume_context
    return output_file, None if run_profiling_stage(resume_context, base_config, status_monitor) else None

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
    """Calcula MRE entre duas saídas de imagem."""
    try:
        def read_img_floats(filepath):
            with open(filepath, 'r') as f:
                for line in f:
                    for part in line.replace(',', ' ').split():
                        try: yield float(part)
                        except: pass
        
        ref_gen, var_gen = read_img_floats(reference_file), read_img_floats(variant_file)
        sum_err, count = 0.0, 0
        for r in ref_gen:
            try: v = next(var_gen)
            except StopIteration: break
            sum_err += abs((r - v) / r) if r != 0 else (1.0 if v != 0 else 0.0)
            count += 1
        return sum_err / count if count > 0 else 1.0
    except Exception as e:
        logging.error(f"Erro ao calcular MRE de Sobel: {e}")
        return None