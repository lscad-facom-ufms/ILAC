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
from  execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines, get_modified_logical_lines
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação Kmeans
KMEANS_CONFIG = {
    "original_file": "axbench/applications/kmeans/src/distance.c",  # agora distance.c!
    "kmeans_file": "axbench/applications/kmeans/src/kmeans.c",
    "distance_file": "axbench/applications/kmeans/src/distance.c",
    "rgbimage_file": "axbench/applications/kmeans/src/rgbimage.c",
    "segmentation_file": "axbench/applications/kmeans/src/segmentation.c",
    "train_data_input": "axbench/applications/kmeans/train.data/input/1.rgb",
    "source_pattern": "distance_*.c",  # variantes de distance.c
    "exe_prefix": "kmeans_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    "input_file_for_variants": "axbench/applications/kmeans/src/distance.c",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    "include_dir": "axbench/applications/kmeans/src",
    "optimization_level": "-O",
}

def prepare_environment(config):
    """Prepara o ambiente específico para a aplicação kmeans"""
    # Copia approx.h se necessário (ajuste se usar approx.h)
    approx_source = config.get("approx_file", "data/reference/approx.h")
    copy_file(approx_source, config["input_dir"])
    return True

def generate_variants(base_config):
    """Gera variantes específicas para kmeans"""
    from generator import generate_variants as gen_vars
    config = {**base_config, **KMEANS_CONFIG}
    print(f"Gerando variantes para kmeans a partir de {config['input_file_for_variants']}")
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
    """Identifica as variantes do kmeans que precisam ser simuladas"""
    config = {**base_config, **KMEANS_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []
    # Hash do original distance.c
    with open(config["distance_file"], "r") as f:
        original_lines = f.readlines()
    _, __, original_physical_to_logical = parse_code(config["distance_file"])
    original_hash = gerar_hash_codigo_logico(original_lines, original_physical_to_logical)
    if original_hash not in executed_variants:
        variants_to_simulate.append((config["distance_file"], original_hash))
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

def compile_kmeans_variant(variant_distance_file, variant_hash, config, status_monitor):
    """Compila uma variante do KMEANS usando o compilador RISCV."""
    variant_id = "original" if variant_distance_file == config["distance_file"] else short_hash(variant_hash)
    status_monitor.update_status(variant_id, "Compilando KMEANS")
    exe_prefix = config.get("exe_prefix", "kmeans_")
    executables_dir = config["executables_dir"]
    optimization = config.get("optimization_level", "-O")
    exe_file = os.path.join(executables_dir, f"{exe_prefix}{variant_hash}")
    include_flags = ["-I", config["input_dir"], "-I", config.get("include_dir", "include")]
    compile_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdcv", optimization, *include_flags,
        config["kmeans_file"],
        variant_distance_file,
        config["rgbimage_file"],
        config["segmentation_file"],
        "-o", exe_file, "-lm"
    ]
    try:
        result = subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        if result.stderr:
            logging.warning(f"[Variante {variant_id}] Avisos: {result.stderr.strip()}")
        logging.info(f"[Variante {variant_id}] Compilado -> {os.path.basename(exe_file)}")
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro compilação: {e.stderr.strip()}")
        status_monitor.update_status(variant_id, "Erro Compilação KMEANS")
        return False, None
    os.chmod(exe_file, 0o755)
    status_monitor.update_status(variant_id, "Compilado KMEANS")
    return True, exe_file

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    config = {**KMEANS_CONFIG, **base_config}
    variant_id = "original" if variant_file == config["original_file"] else short_hash(variant_hash)
    exe_prefix = config["exe_prefix"]
    outputs_dir = config["outputs_dir"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    prof5_results_dir = config["prof5_results_dir"]
    output_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}.rgb")  # AJUSTE PARA .rgb
    time_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    prof5_report_path = os.path.join(prof5_results_dir, f"prof5_results_{variant_hash}.json")
    compiled_ok, exe_file = compile_kmeans_variant(
        variant_file, variant_hash, config, status_monitor
    )
    if not compiled_ok:
        return (None, None)
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
        return (None, None)
    # AJUSTE: use o input correto (ex: .rgb)
    input_file = config.get("train_data_input", "axbench/applications/kmeans/train.data/input/32x32.rgb")
    sim_time = run_spike_simulation(
        exe_file, input_file, output_file,
        spike_log_file, variant_id, status_monitor
    )
    if sim_time is None:
        return (None, None)

    # Converte .rgb para .csv na mesma pasta
    width, height = 512, 512  # ajuste para o tamanho correto da sua imagem!
    csv_output = output_file.replace(".rgb", ".csv")
    try:
        rgb_to_csv(output_file, csv_output, width, height)
    except Exception as e:
        logging.error(f"Erro ao converter .rgb para .csv: {e}")

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
    config = {**base_config, **KMEANS_CONFIG}
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

def rgb_to_csv(rgb_path, csv_path, width, height):
    """Converte um arquivo .rgb (binário) para .csv (cada linha: R,G,B), ignorando metadados no final."""
    pixel_bytes = width * height * 3
    with open(rgb_path, "rb") as f:
        data = f.read(pixel_bytes)
    arr = np.frombuffer(data, dtype=np.uint8)
    if arr.size != width * height * 3:
        raise ValueError(f"Tamanho inesperado: {arr.size} bytes para {width}x{height} pixels")
    arr = arr.reshape((height, width, 3))
    with open(csv_path, "w") as f:
        for row in arr:
            for pixel in row:
                f.write(f"{pixel[0]},{pixel[1]},{pixel[2]}\n")