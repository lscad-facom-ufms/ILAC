import os
import glob
import subprocess
import sys
import shutil
import logging
import json

# Importações do projeto
from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# --- CONFIGURAÇÃO ESPECÍFICA PARA A APLICAÇÃO FFT ---
FFT_CONFIG = {
    # Arquivo alvo das modificações (onde estão as anotações //anotacao:)
    "input_file_for_variants": "data/applications/fft/src/fourier.cpp",
    "fourier_source_file": "data/applications/fft/src/fourier.cpp", 
    
    # Arquivos que são compilados junto mas NÃO sofrem mutação
    "static_sources": [
        "data/applications/fft/src/fft.cpp",      # Contém a função main
        "data/applications/fft/src/complex.cpp"   # Dependência de complex.hpp
    ],
    
    # Argumento de entrada para a simulação (tamanho do vetor)
    "train_data_input": "512", 
    
    # Padrões de arquivos e sufixos
    "source_pattern": "fourier_*.cpp", 
    "exe_prefix": "fourier_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    
    # CRÍTICO: Modelo de energia necessário para o profiling funcionar
    "prof5_model": "data/models/APPROX_1.json", 
    
    # Mapeamento de operações para substituição
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
    
    # Diretórios de include (onde encontrar approx.h, fourier.hpp, etc.)
    "include_dir": "data/applications/fft/src",
    "optimization_level": "-O"
}

def cleanup_variant_files(variant_hash, config):
    """Remove arquivos temporários (logs, dumps), mas preserva resultados (.prof5, .time)."""
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
            logging.error(f"[Variante {variant_id}] Log Spike não encontrado: {spike_log_file}")
            return None
            
        instrucoes_dict = contar_instrucoes_log(spike_log_file)
        if not instrucoes_dict:
            logging.error(f"[Variante {variant_id}] Falha ao contar instruções.")
            return None
            
        # Fallback de segurança para o modelo de energia
        if not prof5_model or not os.path.exists(prof5_model):
            default_model = "data/profiles/riscv_energy_model.json"
            if os.path.exists(default_model):
                prof5_model = default_model
            else:
                logging.error(f"[Variante {variant_id}] Modelo de energia não encontrado: {prof5_model}")
                return None

        resultados_energia = avaliar_modelo_energia(instrucoes_dict, prof5_model)
        if not resultados_energia:
            return None
            
        # Salva o relatório JSON detalhado
        os.makedirs(os.path.dirname(prof5_report_path), exist_ok=True)
        with open(prof5_report_path, 'w') as f:
            json.dump(resultados_energia, f, indent=2, sort_keys=True)
        
        # Salva apenas a latência (tempo) no arquivo .prof5/.time que o run.py espera
        latency_ms = resultados_energia["summary"]["latency_ms"]
        with open(prof5_time_file, 'w') as f:
            f.write(f"{latency_ms}\n")
        
        status_monitor.update_status(variant_id, "Prof5Fake Concluído")
        return latency_ms
        
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5Fake: {e}", exc_info=True)
        return None

def get_pruning_config(base_config):
    """Retorna a configuração para o modo de árvore de poda."""
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
    """Gera o arquivo C++ da variante com as modificações aplicadas."""
    modified_lines_content = list(original_lines)
    for idx in modified_line_indices:
        orig = modified_lines_content[idx]
        transformed = apply_transformation(orig, config["operations_map"])
        # Preserva quebra de linha original
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
    """Copia approx.h para o diretório de variantes."""
    config = {**base_config, **FFT_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    """
    Gera todas as variantes chamando src/gera_variantes.py.
    CORREÇÃO: Configura o PYTHONPATH para garantir que os módulos sejam encontrados.
    """
    config = {**base_config, **FFT_CONFIG}
    output_dir = os.path.abspath(config.get("input_dir"))
    input_path = os.path.abspath(config["input_file_for_variants"])
    executados = os.path.abspath(config.get("executed_variants_file", ""))
    
    os.makedirs(output_dir, exist_ok=True)

    cmd = [
        sys.executable, "src/gera_variantes.py",
        "--input", input_path,
        "--output", output_dir,
        "--strategy", "all"
    ]
    
    if executados and os.path.exists(executados):
        cmd += ["--executados", executados]

    logging.info(f"Gerando variantes FFT: {' '.join(cmd)}")
    
    try:
        # Configuração crítica do ambiente para o subprocesso
        env = os.environ.copy()
        # Define a raiz do projeto (dois níveis acima de src/apps)
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")

        result = subprocess.run(
            cmd, 
            cwd=project_root, 
            capture_output=True, 
            text=True, 
            timeout=1800, # 30 minutos de timeout
            env=env
        )
        
        if result.returncode != 0:
            logging.error(f"Erro no subprocesso gera_variantes:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
            # Tentativa de Fallback (importação direta)
            try:
                logging.info("Tentando fallback via importação direta...")
                sys.path.append(project_root)
                from gera_variantes import main as gera_main
                from config import update_config
                opts = {
                    "input_file": input_path, 
                    "output_folder": output_dir, 
                    "executed_variants_file": executados,
                    "strategy": "all"
                }
                update_config(opts)
                return gera_main()
            except Exception as e:
                logging.error(f"Fallback falhou: {e}")
                return False
        
        # Verifica se gerou arquivos
        pattern = os.path.join(output_dir, config["source_pattern"])
        if not glob.glob(pattern):
            logging.warning("Processo rodou, mas nenhum arquivo foi encontrado no destino.")
            return False

        return True
            
    except Exception as e:
        logging.error(f"Exceção fatal na geração de variantes: {e}")
        return False

def find_variants_to_simulate(base_config):
    """Lista variantes disponíveis para execução."""
    config = {**base_config, **FFT_CONFIG}
    input_dir = config.get("input_dir")
    pattern = os.path.join(input_dir, config["source_pattern"])
    
    # Se não houver variantes, tenta gerar
    if not glob.glob(pattern):
        generate_variants(base_config)
        
    files = sorted(glob.glob(pattern))
    executed = set()
    try:
        executed = set(load_executed_variants(config.get("executed_variants_file", "")))
    except: pass

    source_file = config["input_file_for_variants"]
    try:
        _, _, physical_to_logical = parse_code(source_file)
    except: physical_to_logical = None

    to_run = []
    # Adiciona a versão original
    try:
        with open(source_file, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines(keepends=True)
        h = gerar_hash_codigo_logico(lines, physical_to_logical)
        if h not in executed: 
            to_run.append((source_file, h))
    except: pass

    for f in files:
        if os.path.abspath(f) == os.path.abspath(source_file): continue
        try:
            with open(f, 'r', encoding='utf-8') as fh: 
                lines = fh.readlines()
            h = gerar_hash_codigo_logico(lines, physical_to_logical)
            if h not in executed: 
                to_run.append((f, h))
        except: pass
        
    return to_run, physical_to_logical

def compile_fft_variant(fourier_cpp_to_compile, output_naming_hash, config, status_monitor):
    """Compila a aplicação FFT linkando a variante do fourier.cpp com os estáticos."""
    is_original = (os.path.abspath(fourier_cpp_to_compile) == os.path.abspath(config["fourier_source_file"]))
    variant_id = "original" if is_original else short_hash(output_naming_hash)
    status_monitor.update_status(variant_id, "Compilando FFT")

    exe_prefix = config.get("exe_prefix", "fourier_")
    executables_dir = config["executables_dir"]
    optimization = config.get("optimization_level", "-O")
    
    objects_to_link = []
    # Inclui diretório original e o de variantes (para approx.h)
    include_flags = ["-I", config["include_dir"], "-I", config["input_dir"]]

    # 1. Compila Estáticos (fft.cpp, complex.cpp)
    for static_src in config["static_sources"]:
        base_name = os.path.basename(static_src).replace('.cpp', '')
        obj_name = f"{exe_prefix}{output_naming_hash}_{base_name}.o"
        obj_path = os.path.join(executables_dir, obj_name)
        
        cmd = ["riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags, "-c", static_src, "-o", obj_path, "-lm"]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            status_monitor.update_status(variant_id, f"Erro Compilação {base_name}")
            return False, None
        objects_to_link.append(obj_path)

    # 2. Compila a Variante (fourier.cpp)
    variant_obj = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}_fourier.o")
    cmd_var = ["riscv32-unknown-elf-g++", "-march=rv32imafdc", optimization, *include_flags, "-c", fourier_cpp_to_compile, "-o", variant_obj, "-lm"]
    if subprocess.run(cmd_var, capture_output=True).returncode != 0:
        status_monitor.update_status(variant_id, "Erro Compilação fourier")
        return False, None
    objects_to_link.append(variant_obj)

    # 3. Linkagem Final
    exe_file = os.path.join(executables_dir, f"{exe_prefix}{output_naming_hash}")
    cmd_link = ["riscv32-unknown-elf-g++", "-march=rv32imafdc", *objects_to_link, "-o", exe_file, "-lm"]
    if subprocess.run(cmd_link, capture_output=True).returncode != 0:
        status_monitor.update_status(variant_id, "Erro Linkagem")
        return False, None

    os.chmod(exe_file, 0o755)
    return True, exe_file

def run_profiling_stage(resume_context, base_config, status_monitor):
    """Etapa de profiling (chamada separadamente ou pelo fluxo unificado)."""
    config = {**base_config, **FFT_CONFIG}
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
            # Tenta salvar linhas modificadas (opcional/debug)
            variant_filepath = resume_context["variant_filepath"]
            original_filepath = config["fourier_source_file"]
            if os.path.exists(variant_filepath) and os.path.exists(original_filepath):
                with open(variant_filepath, 'r') as f_v, open(original_filepath, 'r') as f_o:
                    v_lines = f_v.readlines()
                    o_lines = f_o.readlines()
                mod_indices = [i for i, (l1, l2) in enumerate(zip(o_lines, v_lines)) if l1 != l2]
                save_modified_lines_txt(mod_indices, variant_hash, config)
        except Exception: pass

        status_monitor.update_status(variant_id, "Concluída FFT")
        return True
    finally:
        cleanup_variant_files(variant_hash, config)

def simulate_variant(current_variant_filepath, current_variant_hash, base_config, status_monitor, only_spike=False):
    """Executa o fluxo completo: Compilação -> Simulação (Spike) -> Profiling."""
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

    # 1. Compilação
    compiled_ok, exe_file = compile_fft_variant(current_variant_filepath, current_variant_hash, config, status_monitor)
    if not compiled_ok: return (None, None) if only_spike else False

    # 2. Geração de Dump
    if not generate_dump(exe_file, dump_file, variant_id, status_monitor): 
        return (None, None) if only_spike else False

    # 3. Simulação (Spike)
    sim_time = run_spike_simulation(exe_file, config["train_data_input"], spike_output_file, spike_log_file, variant_id, status_monitor)
    if sim_time is None: return (None, None) if only_spike else False
    
    with open(time_file, 'w') as tf: tf.write(f"{sim_time}\n")
    try: os.chmod(time_file, 0o666)
    except: pass

    resume_context = {
        "exe_file": exe_file, "spike_log_file": spike_log_file, "dump_file": dump_file,
        "variant_id": variant_id, "variant_filepath": current_variant_filepath,
        "variant_hash": current_variant_hash, "prof5_time_file": prof5_time_file,
        "prof5_report_path": prof5_report_path,
    }

    if only_spike: return spike_output_file, resume_context
    
    # 4. Profiling (Se only_spike=False, roda imediatamente)
    success = run_profiling_stage(resume_context, base_config, status_monitor)
    return spike_output_file, None if success else None

def save_modified_lines_txt(node_modified_lines, variant_hash, config):
    try:
        linhas_dir = config.get("linhas_modificadas_dir", "storage/linhas_modificadas")
        os.makedirs(linhas_dir, exist_ok=True)
        txt_filepath = os.path.join(linhas_dir, f"linhas_{variant_hash}.txt")
        with open(txt_filepath, 'w', encoding='utf-8') as f:
            for li in node_modified_lines: f.write(f"{li}\n")
        try: os.chmod(txt_filepath, 0o666)
        except: pass
        return txt_filepath
    except Exception: return None

def calculate_custom_error(reference_file, variant_file):
    """Calcula o Mean Relative Error (MRE) comparando a saída complexa (Real Imag)."""
    try:
        with open(reference_file, 'r') as f_ref:
            ref_data = [float(x) for x in f_ref.read().split()]
        with open(variant_file, 'r') as f_var:
            var_data = [float(x) for x in f_var.read().split()]

        total_points = len(ref_data)
        if total_points == 0: return 1.0 

        if len(ref_data) != len(var_data):
            min_len = min(len(ref_data), len(var_data))
            ref_data = ref_data[:min_len]
            var_data = var_data[:min_len]
            total_points = min_len

        sum_relative_error = 0.0
        epsilon = 1e-10 
        for r, v in zip(ref_data, var_data):
            val_ref = abs(r)
            diff = abs(r - v)
            if val_ref < epsilon:
                relative_error = diff / (val_ref + epsilon)
            else:
                relative_error = diff / val_ref
            sum_relative_error += relative_error
        
        return sum_relative_error / total_points
    except Exception as e:
        logging.error(f"[FFT Error] Falha ao calcular MRE: {e}")
        return None