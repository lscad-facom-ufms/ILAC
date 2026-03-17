"""
FFTApp - Aplicação FFT refatorada para herdar de BaseApp
"""

import os
import glob
import subprocess
import sys
import logging
from typing import Dict, List, Tuple, Optional, Any

from src.apps.base import BaseApp
from src.code_parser import parse_code
from src.hash_utils import gerar_hash_codigo_logico
from src.database.variant_tracker import load_executed_variants
from src.utils.file_utils import short_hash, copy_file
from src.execution.simulation import run_spike_simulation


class FFTApp(BaseApp):
    """Aplicação FFT herdando de BaseApp."""
    
    CONFIG: Dict[str, Any] = {
        "original_file": "data/applications/fft/src/fourier.cpp",
        "input_file_for_variants": "data/applications/fft/src/fourier.cpp",
        "fourier_source_file": "data/applications/fft/src/fourier.cpp",
        "static_sources": [
            "data/applications/fft/src/fft.cpp",
            "data/applications/fft/src/complex.cpp"
        ],
        "train_data_input": "4096",
        "source_pattern": "fourier_*.cpp",
        "exe_prefix": "fourier_",
        "output_suffix": ".data",
        "time_suffix": ".time",
        "prof5_suffix": ".prof5",
        "prof5_model": "data/models/APPROX_1.json",
        "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
        "include_dir": "data/applications/fft/src",
        "optimization_level": "-O",
    }
    
    REQUIRED_CONFIG_KEYS = [
        "original_file",
        "input_file_for_variants",
        "operations_map",
        "exe_prefix",
        "train_data_input",
    ]
    
    def __init__(self):
        super().__init__()
    
    def get_config(self) -> Dict[str, Any]:
        return self.CONFIG
    
    def prepare_environment(self, base_config: Dict) -> bool:
        config = self._merge_config(base_config)
        approx_source = config.get("approx_file", "data/reference/approx.h")
        return copy_file(approx_source, config["input_dir"])
    
    def generate_variants(self, base_config: Dict) -> bool:
        config = self._merge_config(base_config)
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
        
        try:
            env = os.environ.copy()
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
            
            result = subprocess.run(
                cmd, cwd=project_root, capture_output=True, text=True,
                timeout=1800, env=env
            )
            
            if result.returncode != 0:
                logging.error(f"Erro no subprocesso gera_variantes:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
                return False
            
            pattern = os.path.join(output_dir, config["source_pattern"])
            if not glob.glob(pattern):
                logging.warning("Processo rodou, mas nenhum arquivo foi encontrado.")
                return False
            
            return True
        except Exception as e:
            logging.error(f"Exceção fatal na geração: {e}")
            return False
    
    def find_variants_to_simulate(self, base_config: Dict) -> Tuple[List[Tuple[str, str]], Dict]:
        config = self._merge_config(base_config)
        input_dir = config.get("input_dir", "storage/variantes")
        pattern = os.path.join(input_dir, config["source_pattern"])
        
        if not glob.glob(pattern):
            self.generate_variants(base_config)
        
        files = sorted(glob.glob(pattern))
        executed = set()
        try:
            executed = set(load_executed_variants(config.get("executed_variants_file", "")))
        except:
            pass
        
        source_file = config["input_file_for_variants"]
        try:
            _, _, physical_to_logical = parse_code(source_file)
        except:
            physical_to_logical = {}
        
        to_run = []
        
        # Adiciona versão original
        try:
            with open(source_file, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines(keepends=True)
            h = gerar_hash_codigo_logico(lines, physical_to_logical)
            if h not in executed:
                to_run.append((source_file, h))
        except:
            pass
        
        for f in files:
            if os.path.abspath(f) == os.path.abspath(source_file):
                continue
            try:
                with open(f, 'r', encoding='utf-8') as fh:
                    lines = fh.readlines()
                h = gerar_hash_codigo_logico(lines, physical_to_logical)
                if h not in executed:
                    to_run.append((f, h))
            except:
                pass
        
        return to_run, physical_to_logical
    
    def _compile_fft_variant(self, fourier_cpp: str, output_hash: str, config: Dict, status_monitor) -> Tuple[bool, Optional[str]]:
        """Compila a aplicação FFT linkando a variante do fourier.cpp com os estáticos."""
        
        is_original = os.path.abspath(fourier_cpp) == os.path.abspath(config["fourier_source_file"])
        variant_id = "original" if is_original else short_hash(output_hash)
        status_monitor.update_status(variant_id, "Compilando FFT")
        
        exe_prefix = config.get("exe_prefix", "fourier_")
        executables_dir = config["executables_dir"]
        optimization = config.get("optimization_level", "-O")
        
        objects_to_link = []
        include_flags = ["-I", config["include_dir"], "-I", config["input_dir"]]
        
        # 1. Compila Estáticos (fft.cpp, complex.cpp)
        for static_src in config.get("static_sources", []):
            base_name = os.path.basename(static_src).replace('.cpp', '')
            obj_name = f"{exe_prefix}{output_hash}_{base_name}.o"
            obj_path = os.path.join(executables_dir, obj_name)
            
            cmd = ["riscv32-unknown-elf-g++", "-march=rv32imafdcv", optimization, *include_flags, "-c", static_src, "-o", obj_path, "-lm"]
            if subprocess.run(cmd, capture_output=True).returncode != 0:
                status_monitor.update_status(variant_id, f"Erro Compilação {base_name}")
                return False, None
            objects_to_link.append(obj_path)
        
        # 2. Compila a Variante (fourier.cpp)
        variant_obj = os.path.join(executables_dir, f"{exe_prefix}{output_hash}_fourier.o")
        cmd_var = ["riscv32-unknown-elf-g++", "-march=rv32imafdcv", optimization, *include_flags, "-c", fourier_cpp, "-o", variant_obj, "-lm"]
        if subprocess.run(cmd_var, capture_output=True).returncode != 0:
            status_monitor.update_status(variant_id, "Erro Compilação fourier")
            return False, None
        objects_to_link.append(variant_obj)
        
        # 3. Linkagem Final
        exe_file = os.path.join(executables_dir, f"{exe_prefix}{output_hash}")
        cmd_link = ["riscv32-unknown-elf-g++", "-march=rv32imafdcv", *objects_to_link, "-o", exe_file, "-lm"]
        if subprocess.run(cmd_link, capture_output=True).returncode != 0:
            status_monitor.update_status(variant_id, "Erro Linkagem")
            return False, None
        
        os.chmod(exe_file, 0o755)
        return True, exe_file
    
    def simulate_variant(
        self,
        variant_file: str,
        variant_hash: str,
        base_config: Dict,
        status_monitor,
        only_spike: bool = False
    ) -> Tuple[Optional[str], Optional[Dict]]:
        config = self._merge_config(base_config)
        
        is_original = os.path.abspath(variant_file) == os.path.abspath(config["fourier_source_file"])
        variant_id = "original" if is_original else short_hash(variant_hash)
        
        exe_prefix = config["exe_prefix"]
        
        spike_output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['output_suffix']}")
        time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
        prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
        spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
        prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
        
        # 1. Compilação
        compiled_ok, exe_file = self._compile_fft_variant(variant_file, variant_hash, config, status_monitor)
        if not compiled_ok:
            return (None, None)
        
        # 2. Simulação Spike
        sim_time = run_spike_simulation(
            exe_file, config["train_data_input"], spike_output_file,
            spike_log_file, variant_id, status_monitor
        )
        if sim_time is None:
            return (None, None)
        
        with open(time_file, 'w') as tf:
            tf.write(f"{sim_time}\n")
        
        resume_context = {
            "exe_file": exe_file,
            "spike_log_file": spike_log_file,
            "variant_id": variant_id,
            "variant_filepath": variant_file,
            "variant_hash": variant_hash,
            "prof5_time_file": prof5_time_file,
            "prof5_report_path": prof5_report_path,
        }
        
        if only_spike:
            return spike_output_file, resume_context
        
        success = self._run_profiling_stage(resume_context, base_config, status_monitor)
        return spike_output_file, None if success else None
    
    def _run_profiling_stage(self, resume_context: Dict, base_config: Dict, status_monitor) -> bool:
        config = self._merge_config(base_config)
        
        try:
            status_monitor.update_status(resume_context["variant_id"], "Iniciando Profiling")
            
            prof5_time = self._run_prof5_fake(
                resume_context["spike_log_file"],
                config.get("prof5_model") or "data/models/APPROX_1.json",
                resume_context["prof5_time_file"],
                resume_context["prof5_report_path"],
                resume_context["variant_id"],
                status_monitor
            )
            
            if prof5_time is None:
                return False
            
            try:
                variant_filepath = resume_context["variant_filepath"]
                original_filepath = config["fourier_source_file"]
                if os.path.exists(variant_filepath) and os.path.exists(original_filepath):
                    with open(variant_filepath, 'r') as f_v, open(original_filepath, 'r') as f_o:
                        v_lines = f_v.readlines()
                        o_lines = f_o.readlines()
                    mod_indices = [i for i, (l1, l2) in enumerate(zip(o_lines, v_lines)) if l1 != l2]
                    self.save_modified_lines_txt(variant_filepath, original_filepath, resume_context["variant_hash"], config)
            except Exception:
                pass
            
            status_monitor.update_status(resume_context["variant_id"], "Concluída FFT")
            return True
        finally:
            self.cleanup_variant_files(resume_context["variant_hash"], config)
    
    def calculate_custom_error(self, reference_file: str, variant_file: str) -> Optional[float]:
        """Calcula Average Relative Error (ARE) para FFT."""
        try:
            with open(reference_file, 'r') as f_ref:
                ref_data = [float(x) for x in f_ref.read().split()]
            with open(variant_file, 'r') as f_var:
                var_data = [float(x) for x in f_var.read().split()]
            
            if len(ref_data) == 0:
                return 0.0
            
            if len(ref_data) != len(var_data):
                min_len = min(len(ref_data), len(var_data))
                ref_data = ref_data[:min_len]
                var_data = var_data[:min_len]
            
            sum_abs_diff = 0.0
            sum_abs_ref = 0.0
            
            for r, v in zip(ref_data, var_data):
                sum_abs_diff += abs(r - v)
                sum_abs_ref += abs(r)
            
            if sum_abs_ref > 0:
                return sum_abs_diff / sum_abs_ref
            return 0.0
        except Exception as e:
            logging.error(f"[FFT Error] Falha ao calcular ARE: {e}")
            return None


# Instância global para compatibilidade com run.py
app = FFTApp()

# Funções wrapper para compatibilidade retroativa
def get_config():
    return app.get_config()

def prepare_environment(base_config):
    return app.prepare_environment(base_config)

def generate_variants(base_config):
    return app.generate_variants(base_config)

def find_variants_to_simulate(base_config):
    return app.find_variants_to_simulate(base_config)

def simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike=False):
    return app.simulate_variant(variant_file, variant_hash, base_config, status_monitor, only_spike)

def cleanup_variant_files(variant_hash, config):
    return app.cleanup_variant_files(variant_hash, config)

def get_pruning_config(base_config):
    return app.get_pruning_config(base_config)

def generate_specific_variant(original_lines, physical_to_logical, modified_line_indices, config):
    return app.generate_specific_variant(original_lines, physical_to_logical, modified_line_indices, config)

def calculate_custom_error(reference_file, variant_file):
    return app.calculate_custom_error(reference_file, variant_file)

def save_modified_lines_txt(variant_file, original_file, variant_hash, config):
    return app.save_modified_lines_txt(variant_file, original_file, variant_hash, config)
