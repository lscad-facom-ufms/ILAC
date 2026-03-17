"""
BlackScholesApp - Aplicação BlackScholes refatorada para herdar de BaseApp
"""

import os
import glob
import logging
import subprocess
import re
import sys
from typing import Dict, List, Tuple, Optional, Any

from src.apps.base import BaseApp
from src.code_parser import parse_code
from src.hash_utils import gerar_hash_codigo_logico
from src.database.variant_tracker import load_executed_variants
from src.utils.file_utils import short_hash, copy_file
from src.execution.compilation import compile_variant
from src.execution.simulation import run_spike_simulation


class BlackScholesApp(BaseApp):
    """Aplicação BlackScholes herdando de BaseApp."""
    
    CONFIG: Dict[str, Any] = {
        "original_file": "data/applications/blackscholes/src/blackscholes.c",
        "train_data_input": "data/applications/blackscholes/src/train.data/input/blackscholesTrain_100K.data",
        "source_pattern": "blackscholes_*.c",
        "exe_prefix": "blackscholes_",
        "output_suffix": ".data",
        "time_suffix": ".time",
        "prof5_suffix": ".prof5",
        "prof5_model": "data/models/APPROX_1.json",
        "input_file_for_variants": "data/applications/blackscholes/src/blackscholes.c",
        "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX', '/': 'FDIVX'},
        "include_dir": "data/applications/blackscholes/src",
        "optimization_level": "-O3",
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
        output_dir = os.path.abspath(config.get("input_dir", "storage/variantes"))
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
            cmd.extend(["--executados", executados])
        
        try:
            env = os.environ.copy()
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            env["PYTHONPATH"] = project_root + os.pathsep + env.get("PYTHONPATH", "")
            subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=1800, env=env)
            pattern = os.path.join(output_dir, config["source_pattern"])
            return len(glob.glob(pattern)) > 0
        except Exception as e:
            logging.error(f"Exceção fatal na geração: {e}")
            return False
    
    def find_variants_to_simulate(self, base_config: Dict) -> Tuple[List[Tuple[str, str]], Dict]:
        config = self._merge_config(base_config)
        executed_variants = load_executed_variants(config["executed_variants_file"])
        variants_to_simulate = []
        
        _, __, physical_to_logical = parse_code(config["original_file"])
        with open(config["original_file"], "r") as f:
            original_hash = gerar_hash_codigo_logico(f.readlines(), physical_to_logical)
        
        if original_hash not in executed_variants:
            variants_to_simulate.append((config["original_file"], original_hash))
        
        pattern = os.path.join(config["input_dir"], config["source_pattern"])
        if not glob.glob(pattern):
            self.generate_variants(base_config)
        
        hash_pattern = re.compile(r"blackscholes_([a-fA-F0-9]+)\.c$")
        for file_path in glob.glob(pattern):
            match = hash_pattern.search(os.path.basename(file_path))
            v_hash = match.group(1) if match else None
            if v_hash and v_hash not in executed_variants:
                variants_to_simulate.append((file_path, v_hash))
        
        return variants_to_simulate, physical_to_logical
    
    def simulate_variant(
        self,
        variant_file: str,
        variant_hash: str,
        base_config: Dict,
        status_monitor,
        only_spike: bool = False
    ) -> Tuple[Optional[str], Optional[Dict]]:
        config = self._merge_config(base_config)
        variant_id = "original" if variant_file == config["original_file"] else short_hash(variant_hash)
        
        exe_prefix = config["exe_prefix"]
        exe_file = os.path.join(config["executables_dir"], f"{exe_prefix}{variant_hash}")
        output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['output_suffix']}")
        time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
        prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
        spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
        prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
        
        # 1. Compilação
        if not compile_variant(variant_file, variant_hash, config, status_monitor):
            return None, None
        
        # 2. Simulação Spike
        sim_time = run_spike_simulation(
            exe_file, config["train_data_input"],
            output_file, spike_log_file,
            variant_id, status_monitor
        )
        if sim_time is None:
            return None, None
        
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
            "output_file": output_file,
        }
        
        if only_spike:
            return output_file, resume_context
        
        # 4. Profiling
        success = self._run_profiling_stage(resume_context, base_config, status_monitor)
        
        if success:
            return output_file, None
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
                    config["original_file"],
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
        """Calcula ARE (Average Relative Error) para BlackScholes."""
        try:
            def read_floats(filepath):
                with open(filepath, 'r') as f:
                    for line in f:
                        if len(line) > 1024:
                            continue
                        for part in line.split():
                            try:
                                yield float(part)
                            except ValueError:
                                pass
            
            ref_gen = read_floats(reference_file)
            var_gen = read_floats(variant_file)
            
            sum_abs_diff = 0.0
            sum_abs_ref = 0.0
            
            for r in ref_gen:
                try:
                    v = next(var_gen)
                except StopIteration:
                    break
                
                sum_abs_diff += abs(r - v)
                sum_abs_ref += abs(r)
            
            if sum_abs_ref > 0:
                return sum_abs_diff / sum_abs_ref
            return 0.0
        except Exception as e:
            logging.error(f"Erro no cálculo de ARE: {e}")
            return None


# Instância global para compatibilidade com run.py
app = BlackScholesApp()

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
