"""
KMeansApp - Aplicação KMeans refatorada para herdar de BaseApp
"""

import os
import glob
import logging
import subprocess
import numpy as np
from typing import Dict, List, Tuple, Optional, Any

from src.apps.base import BaseApp
from src.code_parser import parse_code
from src.hash_utils import gerar_hash_codigo_logico
from src.database.variant_tracker import load_executed_variants
from src.utils.file_utils import short_hash, copy_file
from src.execution.simulation import run_spike_simulation, get_modified_logical_lines


class KMeansApp(BaseApp):
    """Aplicação KMeans herdando de BaseApp."""
    
    CONFIG: Dict[str, Any] = {
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
        "prof5_model": "data/models/APPROX_1.json",
        "input_file_for_variants": "data/applications/kmeans/src/distance.c",
        "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
        "include_dir": "data/applications/kmeans/src",
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
        output_dir = os.path.abspath(config.get("input_dir", "storage/variantes"))
        input_path = os.path.abspath(config["input_file_for_variants"])
        executados = os.path.abspath(config.get("executed_variants_file", ""))
        os.makedirs(output_dir, exist_ok=True)
        
        cmd = [
            "python3", "src/gera_variantes.py",
            "--input", input_path,
            "--output", output_dir,
        ]
        
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
    
    def find_variants_to_simulate(self, base_config: Dict) -> Tuple[List[Tuple[str, str]], Dict]:
        config = self._merge_config(base_config)
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
            self.generate_variants(base_config)
        
        for file in glob.glob(pattern):
            with open(file, "r") as f:
                variant_lines = f.readlines()
            variant_hash = gerar_hash_codigo_logico(variant_lines, original_physical_to_logical)
            if variant_hash not in executed_variants:
                variants_to_simulate.append((file, variant_hash))
        
        return variants_to_simulate, original_physical_to_logical
    
    def _compile_kmeans_variant(self, variant_file: str, variant_hash: str, config: Dict, status_monitor) -> Tuple[bool, Optional[str]]:
        """Compila a aplicação KMeans."""
        
        is_original = os.path.abspath(variant_file) == os.path.abspath(config["distance_file"])
        variant_id = "original" if is_original else short_hash(variant_hash)
        status_monitor.update_status(variant_id, "Compilando KMEANS")
        
        exe_prefix = config.get("exe_prefix", "kmeans_")
        exe_file = os.path.join(config["executables_dir"], f"{exe_prefix}{variant_hash}")
        include_flags = ["-I", config["input_dir"], "-I", config.get("include_dir", "include")]
        
        compile_cmd = [
            "riscv32-unknown-elf-g++", "-march=rv32imafdcv", config.get("optimization_level", "-O"),
            *include_flags, config["kmeans_file"], variant_file,
            config["rgbimage_file"], config["segmentation_file"], "-o", exe_file, "-lm"
        ]
        
        try:
            subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
            os.chmod(exe_file, 0o755)
            return True, exe_file
        except subprocess.CalledProcessError:
            status_monitor.update_status(variant_id, "Erro Compilação KMEANS")
            return False, None
    
    def _rgb_to_csv(self, rgb_path: str, csv_path: str, width: int, height: int):
        """Converte arquivo RGB para CSV."""
        pixel_bytes = width * height * 3
        with open(rgb_path, "rb") as f:
            data = f.read(pixel_bytes)
        arr = np.frombuffer(data, dtype=np.uint8).reshape((height, width, 3))
        with open(csv_path, "w") as f:
            for row in arr:
                for p in row:
                    f.write(f"{p[0]},{p[1]},{p[2]}\n")
    
    def simulate_variant(
        self,
        variant_file: str,
        variant_hash: str,
        base_config: Dict,
        status_monitor,
        only_spike: bool = False
    ) -> Tuple[Optional[str], Optional[Dict]]:
        config = self._merge_config(base_config)
        
        is_original = os.path.abspath(variant_file) == os.path.abspath(config["original_file"])
        variant_id = "original" if is_original else short_hash(variant_hash)
        
        exe_prefix = config["exe_prefix"]
        
        output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}.rgb")
        time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
        prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
        spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
        prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
        
        compiled_ok, exe_file = self._compile_kmeans_variant(variant_file, variant_hash, config, status_monitor)
        if not compiled_ok:
            return (None, None)
        
        input_file = config.get("train_data_input", "data/applications/kmeans/train.data/input/1.rgb")
        sim_time = run_spike_simulation(
            exe_file, input_file, output_file,
            spike_log_file, variant_id, status_monitor
        )
        if sim_time is None:
            return (None, None)
        
        with open(time_file, 'w') as tf:
            tf.write(f"{sim_time}\n")
        
        # Conversão de saída
        csv_output = output_file.replace(".rgb", ".csv")
        try:
            self._rgb_to_csv(output_file, csv_output, 512, 512)
        except:
            pass
        
        resume_context = {
            "exe_file": exe_file,
            "spike_log_file": spike_log_file,
            "variant_id": variant_id,
            "variant_file": variant_file,
            "variant_hash": variant_hash,
            "prof5_time_file": prof5_time_file,
            "prof5_report_path": prof5_report_path,
        }
        
        if only_spike:
            return output_file, resume_context
        
        success = self._run_profiling_stage(resume_context, base_config, status_monitor)
        return output_file, None if success else None
    
    def _run_profiling_stage(self, resume_context: Dict, base_config: Dict, status_monitor) -> bool:
        config = self._merge_config(base_config)
        
        try:
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
                    resume_context["variant_file"],
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
        """Calcula MRE baseado no CSV gerado pelo KMEANS."""
        try:
            ref_csv = reference_file.replace('.rgb', '.csv') if reference_file.endswith('.rgb') else reference_file
            var_csv = variant_file.replace('.rgb', '.csv') if variant_file.endswith('.rgb') else variant_file
            
            with open(ref_csv, 'r') as f1, open(var_csv, 'r') as f2:
                r_lines = f1.readlines()
                v_lines = f2.readlines()
            
            sum_err = 0.0
            count = 0
            
            for r_line, v_line in zip(r_lines, v_lines):
                r_vals = [float(x) for x in r_line.strip().split(',')]
                v_vals = [float(x) for x in v_line.strip().split(',')]
                
                for rv, vv in zip(r_vals, v_vals):
                    if rv != 0:
                        sum_err += abs((rv - vv) / rv)
                    elif vv != 0:
                        sum_err += 1.0
                    count += 1
            
            return sum_err / count if count > 0 else 1.0
        except Exception as e:
            logging.error(f"Erro ao calcular MRE do Kmeans: {e}")
            return None


# Instância global para compatibilidade com run.py
app = KMeansApp()

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
