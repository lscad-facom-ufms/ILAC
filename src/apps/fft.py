import os
import glob
import subprocess
import sys
import shutil
import logging
import json

from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação FFT
FFT_CONFIG = {
    # Arquivos específicos da aplicação
    "fourier_source_file": "data/applications/fft/src/fourier.cpp", 
    "static_sources": [
        "data/applications/fft/src/fft.cpp",
        "data/applications/fft/src/complex.cpp"
    ],
    "train_data_input": "512", 
    
    # Padrões de arquivos
    "source_pattern": "fourier_*.cpp", 
    "exe_prefix": "fourier_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    
    # Parâmetros de geração de variantes
    "input_file_for_variants": "data/applications/fft/src/fourier.cpp",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    
    # Parâmetros específicos de compilação
    "include_dir": "data/applications/fft/src",
    "optimization_level": "-O"
}

def cleanup_variant_files(variant_hash, config):
    """Remove arquivos temporários de uma variante."""
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
    """Executa o prof5fake para análise de energia e performance."""
    try:
        status_monitor.update_status(variant_id, "Executando Prof5Fake")
        
        if not os.path.exists(spike_log_file):
            logging.error(f"[Variante {variant_id}] Log Spike não encontrado: {spike_log_file}")
            return None
            
        instrucoes_dict = contar_instrucoes_log(spike_log_file)
        if not instrucoes_dict:
            return None
            
        resultados_energia = avaliar_modelo_energia(instrucoes_dict, prof5_model)
        if not resultados_energia:
            return None
            
        with open(prof5_report_path, 'w') as f:
            json.dump(resultados_energia, f, indent=2, sort_keys=True)
        os.chmod(prof5_report_path, 0o666)
        
        latency_ms = resultados_energia["summary"]["latency_ms"]
        
        with open(prof5_time_file, 'w') as f:
            f.write(f"{latency_ms}\n")
        os.chmod(prof5_time_file, 0o666)
        
        status_monitor.update_status(variant_id, "Prof5Fake Concluído")
        return latency_ms
        
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5Fake: {e}", exc_info=True)
        return None

def get_pruning_config(base_config):
    """Retorna informações necessárias para poda (mesma forma usada em jmeint)."""
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
    """Gera um arquivo variante de fourier.cpp a partir das linhas modificadas."""
    modified_lines_content = list(original_lines)
    for idx in modified_line_indices:
        orig = modified_lines_content[idx]
        transformed = apply_transformation(orig, config["operations_map"])
        # preserva newline
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
    """Copia arquivos necessários (ex: approx.h) para o workspace de variantes."""
    config = {**base_config, **FFT_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    """Invoca o gerador de variantes (gera_variantes.py) para Fourier."""
    from gera_variantes import main as gera_main
    config = {**base_config, **FFT_CONFIG}
    output_dir = os.path.abspath(config.get("input_dir"))
    input_path = os.path.abspath(config["input_file_for_variants"])
    executados = os.path.abspath(config.get("executed_variants_file", ""))

    cmd = [
        sys.executable, "src/gera_variantes.py",
        "--input", input_path,
        "--output", output_dir
    ]
    if executados:
        cmd += ["--executados", executados]

    logging.info(f"Gerando variantes FFT: {' '.join(cmd)}")
    # Chamada direta (para aproveitar retornos/controle) - encaixe com jmeint
    try:
        result = subprocess.run(cmd, cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")), capture_output=True, text=True, timeout=300)
        logging.debug(f"gera_variantes returncode={result.returncode}")
        if result.stdout: logging.debug(result.stdout[:1000])
        if result.stderr: logging.debug(result.stderr[:1000])
    except Exception as e:
        logging.warning(f"Falha ao executar gerador de variantes FFT: {e}")
        # fallback: tentar chamar a função diretamente (se disponível)
        try:
            ok = gera_main({"input_file": input_path, "output_folder": output_dir, "executed_variants_file": executados})
            return ok
        except Exception:
            return False

    # Confirma se existem arquivos no output_dir; se não, procura no repositório e copia
    pattern = os.path.join(output_dir, config["source_pattern"])
    if not glob.glob(pattern):
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        discovered = glob.glob(os.path.join(project_root, "**", config["source_pattern"]), recursive=True)
        discovered = [p for p in discovered if os.path.abspath(os.path.dirname(p)) != os.path.abspath(output_dir)]
        if discovered:
            os.makedirs(output_dir, exist_ok=True)
            for src in discovered:
                dst = os.path.join(output_dir, os.path.basename(src))
                try:
                    shutil.copy2(src, dst)
                except Exception:
                    logging.debug(f"Não foi possível copiar {src} -> {dst}")

    return bool(glob.glob(pattern))

def find_variants_to_simulate(base_config):
    """Encontra fourier_*.cpp no input_dir e retorna (lista, physical_to_logical)."""
    config = {**base_config, **FFT_CONFIG}
    input_dir = config.get("input_dir")
    pattern = os.path.join(input_dir, config["source_pattern"])
    files = sorted(glob.glob(pattern))
    logging.debug(f"Encontrados {len(files)} arquivos com padrão {pattern}")

    executed = set()
    try:
        executed = set(load_executed_variants(config.get("executed_variants_file", "")))
    except Exception:
        logging.debug("Arquivo de executados não encontrado ou inválido")

    # Mapa físico->lógico do original
    source_file = config["input_file_for_variants"]
    try:
        _, _, physical_to_logical = parse_code(source_file)
    except Exception as e:
        logging.error(f"Falha ao parsear {source_file}: {e}", exc_info=True)
        physical_to_logical = None

    to_run = []
    # Adicionar a versão original (se ainda não executada)
    original_hash = gerar_hash_codigo_logico(open(source_file, 'r', encoding='utf-8').read().splitlines(keepends=True), physical_to_logical)
    if original_hash not in executed:
        to_run.append((source_file, original_hash))

    for f in files:
        if os.path.abspath(f) == os.path.abspath(source_file):
            continue
        with open(f, 'r', encoding='utf-8') as fh:
            variant_lines = fh.readlines()
        variant_hash = gerar_hash_codigo_logico(variant_lines, physical_to_logical)
        if variant_hash not in executed:
            to_run.append((f, variant_hash))

    logging.info(f"TOTAL DE VARIANTES ENCONTRADAS: {len(to_run)}")
    return to_run, physical_to_logical

def compile_fft_variant(fourier_cpp_to_compile, output_naming_hash, config, status_monitor):
    """Compilação especializada: Estáticos + Variante."""
    is_original = (os.path.abspath(fourier_cpp_to_compile) == os.path.abspath(config["fourier_source_file"]))
    variant_id = "original" if is_original else short_hash(output_naming_hash)
    status_monitor.update_status(variant_id, "Compilando FFT")

    exe_prefix = config.get("exe_prefix", "fourier_")
    executables_dir = config["executables_dir"]
    optimization = config.get("optimization_level", "-O")
    
    objects_to_link = []
    include_flags = ["-I", config["include_dir"], "-I", config["input_dir"]]

    # 1. Compilar arquivos estáticos
    for static_src in config["static_sources"]:
        base_name = os.path.basename(static_src).replace('.cpp', '')
        obj_name = f"{exe_prefix}{output_naming_hash}_{base_name}.o"
        obj_path = os.path.join(executables_dir, obj_name)
        
        compile_static_cmd = [
            "riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags,
            "-c", static_src, "-o", obj_path, "-lm"
        ]
        
        try:
            subprocess.run(compile_static_cmd, check=True, capture_output=True, text=True)
            objects_to_link.append(obj_path)
        except subprocess.CalledProcessError:
            status_monitor.update_status(variant_id, f"Erro Compilação ({base_name})")
            return False, None

    # 2. Compilar a variante
    variant_obj_name = f"{exe_prefix}{output_naming_hash}_fourier.o"
    variant_obj_path = os.path.join(executables_dir, variant_obj_name)
    
    compile_variant_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags,
        "-c", fourier_cpp_to_compile, "-o", variant_obj_path, "-lm"
    ]
    
    try:
        subprocess.run(compile_variant_cmd, check=True, capture_output=True, text=True)
        objects_to_link.append(variant_obj_path)
    except subprocess.CalledProcessError:
        status_monitor.update_status(variant_id, "Erro Compilação (fourier)")
        return False, None

    # 3. Linkar tudo
    exe_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}")
    link_cmd = [
        "riscv32-unknown-elf-g++", "-march=rv32imafdc",
        *objects_to_link, "-o", exe_file, "-lm"
    ]
    
    try:
        subprocess.run(link_cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError:
        status_monitor.update_status(variant_id, "Erro Linkagem FFT")
        return False, None

    os.chmod(exe_file, 0o755)
    return True, exe_file

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Executa profiling (Prof5Fake)."""
    config = {**base_config, **FFT_CONFIG}
    spike_log_file = resume_context["spike_log_file"]
    variant_id = resume_context["variant_id"]
    prof5_time_file = resume_context["prof5_time_file"]
    prof5_report_path = resume_context["prof5_report_path"]
    variant_hash = resume_context["variant_hash"]
    
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
            original_filepath = config["fourier_source_file"]
            with open(variant_filepath, 'r') as f_variant, open(original_filepath, 'r') as f_original:
                variant_lines = f_variant.readlines()
                original_lines = f_original.readlines()
            modified_indices = [i for i, (line1, line2) in enumerate(zip(original_lines, variant_lines)) if line1 != line2]
            save_modified_lines_txt(modified_indices, variant_hash, config)
        except Exception:
            pass

        status_monitor.update_status(variant_id, "Concluída FFT")
        return True
    finally:
        cleanup_variant_files(variant_hash, config)

def simulate_variant(current_variant_filepath, current_variant_hash, base_config, status_monitor, only_spike=False):
    """Simula uma combinação de FFT."""
    config = {**base_config, **FFT_CONFIG}
    is_original = (os.path.abspath(current_variant_filepath) == os.path.abspath(config["fourier_source_file"]))
    variant_id = "original" if is_original else short_hash(current_variant_hash)

    exe_prefix = config["exe_prefix"]
    outputs_dir = config["outputs_dir"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    prof5_results_dir = config["prof5_results_dir"]

    spike_output_file = os.path.join(outputs_dir, f"{exe_prefix}{current_variant_hash}{config['output_suffix']}")
    time_file = os.path.join(outputs_dir, f"{exe_prefix}{current_variant_hash}{config['time_suffix']}")
    prof5_time_file = os.path.join(outputs_dir, f"{exe_prefix}{current_variant_hash}{config['prof5_suffix']}")
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{current_variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{current_variant_hash}.txt")
    prof5_report_path = os.path.join(prof5_results_dir, f"prof5_results_{current_variant_hash}.json")

    compiled_ok, exe_file = compile_fft_variant(current_variant_filepath, current_variant_hash, config, status_monitor)
    if not compiled_ok: return (None, None) if only_spike else False

    if not generate_dump(exe_file, dump_file, variant_id, status_monitor): 
        return (None, None) if only_spike else False

    sim_time = run_spike_simulation(exe_file, config["train_data_input"], spike_output_file, spike_log_file, variant_id, status_monitor)
    if sim_time is None: return (None, None) if only_spike else False
    
    with open(time_file, 'w') as tf: tf.write(f"{sim_time}\n")
    os.chmod(time_file, 0o666)

    resume_context = {
        "exe_file": exe_file, "spike_log_file": spike_log_file, "dump_file": dump_file,
        "variant_id": variant_id, "variant_filepath": current_variant_filepath,
        "variant_hash": current_variant_hash, "prof5_time_file": prof5_time_file,
        "prof5_report_path": prof5_report_path,
    }

    if only_spike: return spike_output_file, resume_context
    return spike_output_file, None if run_profiling_stage(resume_context, base_config, status_monitor) else None

def save_modified_lines_txt(node_modified_lines, variant_hash, config):
    """Salva os índices das linhas modificadas."""
    try:
        linhas_dir = config.get("linhas_modificadas_dir", "storage/linhas_modificadas")
        os.makedirs(linhas_dir, exist_ok=True)
        txt_filename = f"linhas_{variant_hash}.txt"
        txt_filepath = os.path.join(linhas_dir, txt_filename)
        with open(txt_filepath, 'w', encoding='utf-8') as f:
            for li in node_modified_lines:
                f.write(f"{li}\n")
        os.chmod(txt_filepath, 0o666)
        return txt_filepath
    except Exception as e:
        logging.debug(f"Erro ao salvar linhas modificadas: {e}")
        return None

def calculate_custom_error(reference_file, variant_file):
    """
    Calcula o Mean Relative Error (MRE) para FFT.
    MRE = (1/N) * sum( |Ref - Var| / |Ref| )
    """
    try:
        # Lê os arquivos convertendo para float (FFT gera saídas numéricas flutuantes)
        with open(reference_file, 'r') as f_ref:
            ref_data = [float(x) for x in f_ref.read().split()]
            
        with open(variant_file, 'r') as f_var:
            var_data = [float(x) for x in f_var.read().split()]

        total_points = len(ref_data)
        
        if total_points == 0:
            logging.error("[FFT Error] Arquivo de referência vazio.")
            return 1.0 

        # Truncagem se tamanhos diferem (comportamento padrão de robustez)
        if len(ref_data) != len(var_data):
            logging.warning(f"[FFT Warning] Tamanhos diferem: Ref={len(ref_data)}, Var={len(var_data)}. Truncando.")
            min_len = min(len(ref_data), len(var_data))
            ref_data = ref_data[:min_len]
            var_data = var_data[:min_len]
            total_points = min_len

        # Cálculo do MRE acumulado
        sum_relative_error = 0.0
        epsilon = 1e-10 # Pequeno valor para estabilidade na divisão

        for r, v in zip(ref_data, var_data):
            val_ref = abs(r)
            diff = abs(r - v)
            
            # Tratamento para divisão por zero se o valor de referência for 0
            if val_ref < epsilon:
                # Se ref é 0, usamos diferença absoluta amortecida ou 0 se ambos forem 0
                relative_error = diff / (val_ref + epsilon)
            else:
                relative_error = diff / val_ref
            
            sum_relative_error += relative_error
        
        mre = sum_relative_error / total_points
        
        logging.info(f"[FFT Metric] MRE: {mre:.6f}")
        
        return mre

    except ValueError as e:
        logging.error(f"[FFT Error] Erro de formatação numérica nos arquivos: {e}")
        return None
    except Exception as e:
        logging.error(f"[FFT Error] Falha ao calcular MRE: {e}")
        return None