import os
import glob
import logging
import json
import subprocess
import shutil
import numpy as np
from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines, get_modified_logical_lines
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação Sobel
SOBEL_CONFIG = {
    "original_file": "axbench/applications/sobel/src/convolution.cpp",
    "sobel_file": "axbench/applications/sobel/src/sobel.cpp",
    "convolution_file": "axbench/applications/sobel/src/convolution.cpp",
    "rgb_image_file": "axbench/applications/sobel/src/rgb_image.cpp",
    "approx_file": "data/reference/approx.h",
    "source_pattern": "convolution_*.cpp",
    "exe_prefix": "sobel_",
    "output_suffix": ".csv",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    "input_file_for_variants": "axbench/applications/sobel/src/convolution.cpp",
    "input_file": "axbench/applications/sobel/train.data/input/32x32.rgb",  # <-- Adicione esta linha!
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    "include_dir": "axbench/applications/sobel/src",
    "optimization_level": "-O",
}

def prepare_environment(config):
    """Prepara o ambiente específico para a aplicação sobel"""
    approx_source = config.get("approx_file", "data/reference/approx.h")
    copy_file(approx_source, config["input_dir"])
    return True

def generate_variants(base_config):
    """Gera variantes específicas para sobel"""
    from generator import generate_variants as gen_vars
    config = {**base_config, **SOBEL_CONFIG}
    print(f"Gerando variantes para sobel a partir de {config['input_file_for_variants']}")
    lines, modifiable_lines, physical_to_logical = parse_code(config["input_file_for_variants"])
    return gen_vars(
        lines,
        modifiable_lines,
        physical_to_logical,
        config["operations_map"],
        config["input_dir"],
        os.path.basename(config["input_file_for_variants"]),
        config["executed_variants_file"]
    )

def find_variants_to_simulate(base_config):
    """Identifica as variantes do sobel que precisam ser simuladas"""
    config = {**base_config, **SOBEL_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []
    # Hash do original sobel.cpp
    # Hash do original convolution.cpp (base das variantes)
    with open(config["original_file"], "r") as f:
        original_lines = f.readlines()
    _, __, original_physical_to_logical = parse_code(config["original_file"])
    original_hash = gerar_hash_codigo_logico(original_lines, original_physical_to_logical)
    if original_hash not in executed_variants:
        variants_to_simulate.append((config["original_file"], original_hash))
        print(f"Versão original será simulada (hash: {short_hash(original_hash)})")
    else:
        print(f"Versão original já foi executada (hash: {short_hash(original_hash)})")
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

def compile_sobel_variant(variant_sobel_file, variant_hash, config, status_monitor):
    optimization = config.get("optimization_level", "-O")
    # Adicione o diretório onde está approx.h (diretório da variante)
    include_flags = [
        "-I" + config["include_dir"],
        "-I" + os.path.dirname(variant_sobel_file)  # <-- Adiciona o diretório da variante
    ]
    exe_file = os.path.join(config["executables_dir"], f"{config['exe_prefix']}{variant_hash}")
    variant_id = "original" if variant_sobel_file == config["original_file"] else short_hash(variant_hash)

    if variant_sobel_file == config["original_file"]:
        convolution_src = config["original_file"]
    else:
        convolution_src = variant_sobel_file

    compile_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdcv", optimization, *include_flags,
        convolution_src,
        config["rgb_image_file"],
        config["sobel_file"],
        "-o", exe_file, "-lm"
    ]
    try:
        result = subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        if result.stderr:
            logging.warning(f"[Variante {variant_id}] Avisos: {result.stderr.strip()}")
        logging.info(f"[Variante {variant_id}] Compilado -> {os.path.basename(exe_file)}")
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro compilação: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Compilação SOBEL")
        return False, None
    os.chmod(exe_file, 0o755)
    status_monitor.update_status(variant_id, "Compilado SOBEL")
    return True, exe_file

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    config = {**SOBEL_CONFIG, **base_config}
    variant_id = "original" if variant_file == config["original_file"] else short_hash(variant_hash)
    exe_prefix = config["exe_prefix"]
    outputs_dir = config["outputs_dir"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    prof5_results_dir = config["prof5_results_dir"]
    output_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}.rgb")
    time_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    prof5_report_path = os.path.join(prof5_results_dir, f"prof5_results_{variant_hash}.json")
    compiled_ok, exe_file = compile_sobel_variant(
        variant_file, variant_hash, config, status_monitor
    )
    if not compiled_ok:
        return (None, None)
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
        return (None, None)
    # Use o input_file correto do config
    input_file = config.get("input_file", "axbench/applications/sobel/train.data/input/32x32.rgb")
    if not os.path.exists(input_file):
        logging.error(f"Arquivo de entrada não encontrado: {input_file}")
        return (None, None)
    sim_time = run_spike_simulation(
        exe_file, input_file, output_file,
        spike_log_file, variant_id, status_monitor
    )
    if sim_time is None:
        return (None, None)
    with open(time_file, 'w') as tf:
        tf.write(f"{sim_time}\n")
    os.chmod(time_file, 0o666)
    resume_context = {
        "exe_file": exe_file,
        "spike_log_file": spike_log_file,
        "dump_file": dump_file,
        "variant_id": variant_id,
        "variant_file": variant_file,
        "variant_hash": variant_hash,
        "prof5_time_file": prof5_time_file,
        "prof5_report_path": prof5_report_path,
    }
    if only_spike:
        return output_file, resume_context
    success = run_profiling_stage(resume_context, base_config, status_monitor)
    if success:
        status_monitor.update_status(variant_id, "Concluída")
        save_modified_lines_txt(
            variant_file,
            config["original_file"],
            variant_hash,
            config
        )
        return output_file, None
    else:
        status_monitor.update_status(variant_id, "Falha no Profiling")
        return None, None

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Executa apenas a parte de profiling (Prof5 Fake) da simulação."""
    config = {**base_config, **SOBEL_CONFIG}
    prof5_time = run_prof5_fake(
        resume_context["spike_log_file"],
        config.get("prof5_model", None),
        resume_context["prof5_time_file"],
        resume_context["prof5_report_path"],
        resume_context["variant_id"],
        status_monitor
    )
    return prof5_time is not None

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
        tempo_total = summary["latency_ms"]
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

def cleanup_variant_files(variant_hash, config):
    """Remove arquivos temporários gerados durante a simulação de uma variante."""
    exe_prefix = config["exe_prefix"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    for f in [spike_log_file, dump_file]:
        if os.path.exists(f):
            try:
                os.remove(f)
            except OSError as e:
                logging.error(f"Erro ao remover arquivo temporário {f}: {e}")

def save_modified_lines_txt(variant_file, original_file, variant_hash, config):
    """
    Salva os índices das linhas lógicas modificadas entre o original e a variante.
    """
    try:
        if not os.path.exists(original_file) or not os.path.exists(variant_file):
            logging.error(f"Arquivo não encontrado: {original_file} ou {variant_file}")
            return None
        with open(original_file, 'r') as f_orig, open(variant_file, 'r') as f_var:
            original_lines = f_orig.readlines()
            variant_lines = f_var.readlines()
        _, __, physical_to_logical = parse_code(original_file)
        logical_indices = get_modified_logical_lines(original_lines, variant_lines, physical_to_logical)

        linhas_dir = config.get("linhas_modificadas_dir", "storage/linhas_modificadas")
        txt_filename = f"linhas_{variant_hash}.txt"
        txt_filepath = os.path.join(linhas_dir, txt_filename)
        os.makedirs(linhas_dir, exist_ok=True)
        with open(txt_filepath, 'w', encoding='utf-8') as f:
            for line_index in logical_indices:
                f.write(f"{line_index}\n")
        os.chmod(txt_filepath, 0o666)
        logging.info(f"Índices das linhas modificadas salvos: {txt_filepath}")
        return txt_filepath
    except Exception as e:
        logging.error(f"Erro ao salvar índices das linhas modificadas: {e}", exc_info=True)
        return None