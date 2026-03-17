"""
JMeintApp - Aplicação JMeint refatorada para herdar de BaseApp
"""

import os
import glob
import logging
import subprocess
import sys
from typing import Dict, List, Tuple, Optional, Any

from src.apps.base import BaseApp
from src.code_parser import parse_code
from src.hash_utils import gerar_hash_codigo_logico
from src.database.variant_tracker import load_executed_variants
from src.utils.file_utils import short_hash, copy_file
from src.execution.simulation import run_spike_simulation


class JMeintApp(BaseApp):
    """Aplicação JMeint herdando de BaseApp."""
    
    CONFIG: Dict[str, Any] = {
        "original_file": "data/applications/jmeint/src/jmeint.cpp",
        "jmeint_main_file": "data/applications/jmeint/src/jmeint.cpp",
        "tritri_source_file": "data/applications/jmeint/src/tritri.cpp",
        "train_data_input": "data/applications/jmeint/train.data/input/jmeint_10k.data",
        "source_pattern": "tritri_*.cpp",
        "exe_prefix": "jmeint_",
        "output_suffix": ".data",
        "time_suffix": ".time",
        "prof5_suffix": ".prof5",
        "prof5_model": "data/models/APPROX_1.json",
        "input_file_for_variants": "data/applications/jmeint/src/tritri.cpp",
        "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
        "include_dir": "data/applications/jmeint/src",
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
        os.makedirs(config["input_dir"], exist_ok=True)
        return copy_file(approx_source, config["input_dir"])
    
    def generate_variants(self, base_config: Dict) -> bool:
        config = self._merge_config(base_config)
        
        try:
            input_path = os.path.abspath(config["input_file_for_variants"])
            output_path = os.path.abspath(config["input_dir"])
            executados_path = os.path.abspath(config["executed_variants_file"])
            
            cmd = [
                sys.executable, "src/gera_variantes.py",
                "--input", input_path,
                "--output", output_path,
                "--executados", executados_path,
                "--strategy", "all"
            ]
            
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=1800)
            
            pattern = os.path.join(config["input_dir"], "tritri_*.cpp")
            generated_files = glob.glob(pattern)
            return len(generated_files) > 0
        except Exception as e:
            logging.error(f"Erro na geração: {e}")
            return False
    
    def find_variants_to_simulate(self, base_config: Dict) -> Tuple[List[Tuple[str, str]], Dict]:
        config = self._merge_config(base_config)
        executed_variants = load_executed_variants(config["executed_variants_file"])
        variants_to_simulate = []
        
        tritri_original_path = config["tritri_source_file"]
        with open(tritri_original_path, "r") as f:
            tritri_original_lines = f.readlines()
        _, __, tritri_original_physical_to_logical = parse_code(tritri_original_path)
        
        tritri_original_hash = gerar_hash_codigo_logico(tritri_original_lines, tritri_original_physical_to_logical)
        
        if tritri_original_hash not in executed_variants:
            variants_to_simulate.append((tritri_original_path, tritri_original_hash))
        
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
    
    def _compile_jmeint_variant(self, main_cpp: str, kernel_cpp: str, output_hash: str, config: Dict, status_monitor) -> Tuple[bool, Optional[str]]:
        """Compilação especializada: jmeint.cpp (fixo) + tritri.cpp (variante)."""
        
        is_original = os.path.abspath(kernel_cpp) == os.path.abspath(config["tritri_source_file"])
        variant_id = "original" if is_original else short_hash(output_hash)
        status_monitor.update_status(variant_id, "Compilando JMEINT")
        
        exe_prefix = config.get("exe_prefix", "jmeint_")
        executables_dir = config["executables_dir"]
        optimization = config.get("optimization_level", "-O")
        
        main_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_hash}_jmeint.o")
        kernel_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_hash}_tritri.o")
        exe_file = os.path.join(executables_dir, f"{exe_prefix}{output_hash}")
        
        include_flags = ["-I", config["include_dir"], "-I", config["input_dir"]]
        
        # 1. Compilar Main
        compile_main_cmd = [
            "riscv32-unknown-elf-g++", "-march=rv32imafdcv", optimization, *include_flags,
            "-c", main_cpp, "-o", main_obj_file, "-lm"
        ]
        try:
            subprocess.run(compile_main_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"[{variant_id}] Erro compilação jmeint: {e.stderr.strip()}")
            status_monitor.update_status(variant_id, "Erro Compilação (jmeint.cpp)")
            return False, None
        
        # 2. Compilar Kernel
        compile_kernel_cmd = [
            "riscv32-unknown-elf-g++", "-march=rv32imafdcv", optimization, *include_flags,
            "-c", kernel_cpp, "-o", kernel_obj_file, "-lm"
        ]
        try:
            subprocess.run(compile_kernel_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"[{variant_id}] Erro compilação tritri: {e.stderr.strip()}")
            status_monitor.update_status(variant_id, "Erro Compilação (tritri)")
            return False, None
        
        # 3. Linkar
        link_cmd = [
            "riscv32-unknown-elf-g++", "-march=rv32imafdcv",
            main_obj_file, kernel_obj_file, "-o", exe_file, "-lm"
        ]
        try:
            subprocess.run(link_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"[{variant_id}] Erro linkagem: {e.stderr.strip()}")
            status_monitor.update_status(variant_id, "Erro Linkagem JMEINT")
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
        
        is_original = os.path.abspath(variant_file) == os.path.abspath(config["tritri_source_file"])
        variant_id = "original" if is_original else short_hash(variant_hash)
        
        exe_prefix = config.get("exe_prefix", "jmeint_")
        
        spike_output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['output_suffix']}")
        time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
        prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
        spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
        prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
        
        main_to_compile = config["jmeint_main_file"]
        kernel_to_compile = variant_file
        
        compiled_ok, exe_file = self._compile_jmeint_variant(
            main_to_compile, kernel_to_compile, variant_hash, config, status_monitor
        )
        if not compiled_ok:
            return (None, None)
        
        sim_time = run_spike_simulation(
            exe_file, config["train_data_input"], spike_output_file,
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
            "variant_id": variant_id,
            "variant_filepath": variant_file,
            "variant_hash": variant_hash,
            "prof5_time_file": prof5_time_file,
            "prof5_report_path": prof5_report_path,
        }
        
        if only_spike:
            return spike_output_file, resume_context
        
        success = self._run_profiling_stage(resume_context, base_config, status_monitor)
        
        if success:
            return spike_output_file, None
        else:
            return None, None
    
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
                self.save_modified_lines_txt(
                    resume_context["variant_filepath"],
                    config["tritri_source_file"],
                    resume_context["variant_hash"],
                    config
                )
            except Exception:
                pass
            
            status_monitor.update_status(resume_context["variant_id"], "Concluída")
            return True
        finally:
            self.cleanup_variant_files(resume_context["variant_hash"], config)
    
    def calculate_custom_error(self, reference_file: str, variant_file: str) -> Optional[float]:
        """Calcula Miss Rate para JMeint."""
        try:
            with open(reference_file, 'r') as f_ref:
                ref_data = [int(x) for x in f_ref.read().split()]
            
            with open(variant_file, 'r') as f_var:
                var_data = [int(x) for x in f_var.read().split()]
            
            total_points = len(ref_data)
            if total_points == 0:
                return 1.0
            
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


# Instância global para compatibilidade com run.py
app = JMeintApp()

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

def save_modified_lines_txt(node_modified_lines, variant_hash, config):
    """Wrapper especial para o modo árvore."""
    return app.save_modified_lines_txt(
        config.get("input_file_for_variants"),
        config.get("tritri_source_file"),
        variant_hash,
        config
    )
