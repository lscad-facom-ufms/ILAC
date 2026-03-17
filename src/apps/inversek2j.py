"""
InverseK2JApp - Aplicação InverseK2J refatorada para herdar de BaseApp
"""

import os
import glob
import logging
import subprocess
from typing import Dict, List, Tuple, Optional, Any

from src.apps.base import BaseApp
from src.code_parser import parse_code
from src.hash_utils import gerar_hash_codigo_logico
from src.database.variant_tracker import load_executed_variants
from src.utils.file_utils import short_hash, copy_file
from src.execution.simulation import run_spike_simulation


class InverseK2JApp(BaseApp):
    """Aplicação InverseK2J/Kinematics herdando de BaseApp."""
    
    CONFIG: Dict[str, Any] = {
        "original_file": "data/applications/inversek2j/src/inversek2j.cpp",
        "inversek2j_main_file": "data/applications/inversek2j/src/inversek2j.cpp",
        "kinematics_source_file": "data/applications/inversek2j/src/kinematics.cpp",
        "train_data_input": "data/applications/inversek2j/train.data/input/theta_100K.data",
        "source_pattern": "kinematics_*.cpp",
        "exe_prefix": "inversek2j_",
        "output_suffix": ".data",
        "time_suffix": ".time",
        "prof5_suffix": ".prof5",
        "prof5_model": "data/models/APPROX_1.json",
        "input_file_for_variants": "data/applications/inversek2j/src/kinematics.cpp",
        "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},
        "include_dir": "data/applications/inversek2j/src",
        "optimization_level": "-O",
        "strategy": "all",
        "max_variantes": 10000,
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
            
            strategy = config.get("strategy", "one_hot")
            max_vars = str(config.get("max_variantes", 100))
            
            cmd = [
                "python3", "src/gera_variantes.py",
                "--input", input_path,
                "--output", output_path,
                "--executados", executados_path,
                "--strategy", strategy,
                "--max_variantes", max_vars
            ]
            
            project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            subprocess.run(cmd, cwd=project_root, capture_output=True, text=True, timeout=1800)
            
            pattern = os.path.join(config["input_dir"], "kinematics_*.cpp")
            generated_files = glob.glob(pattern)
            logging.info(f"{len(generated_files)} variantes encontradas/geradas no workspace.")
            return len(generated_files) > 0
        except Exception as e:
            logging.error(f"Erro na geração de variantes: {e}")
            return False
    
    def find_variants_to_simulate(self, base_config: Dict) -> Tuple[List[Tuple[str, str]], Dict]:
        config = self._merge_config(base_config)
        executed_variants = load_executed_variants(config["executed_variants_file"])
        variants_to_simulate = []
        
        kinematics_original_path = config["kinematics_source_file"]
        with open(kinematics_original_path, "r") as f:
            original_lines = f.readlines()
        _, __, original_physical_to_logical = parse_code(kinematics_original_path)
        
        original_hash = gerar_hash_codigo_logico(original_lines, original_physical_to_logical)
        
        if original_hash not in executed_variants:
            variants_to_simulate.append((kinematics_original_path, original_hash))
            logging.info(f"Versão original de KINEMATICS será simulada (hash: {short_hash(original_hash)})")
        
        variant_pattern = os.path.join(config["input_dir"], config["source_pattern"])
        variant_files = glob.glob(variant_pattern)
        
        for variant_file_path in variant_files:
            if os.path.abspath(variant_file_path) == os.path.abspath(kinematics_original_path):
                continue
            
            with open(variant_file_path, "r") as f:
                variant_lines = f.readlines()
            
            variant_hash = gerar_hash_codigo_logico(variant_lines, original_physical_to_logical)
            
            if variant_hash not in executed_variants:
                variants_to_simulate.append((variant_file_path, variant_hash))
        
        return variants_to_simulate, original_physical_to_logical
    
    def _compile_kinematics_variant(self, main_cpp: str, kernel_cpp: str, output_hash: str, config: Dict, status_monitor) -> Tuple[bool, Optional[str]]:
        """Compilação especializada: main.cpp (fixo) + kernel.cpp (variante)."""
        
        is_original = os.path.abspath(kernel_cpp) == os.path.abspath(config["kinematics_source_file"])
        variant_id = "original" if is_original else short_hash(output_hash)
        status_monitor.update_status(variant_id, "Compilando Kinematics")
        
        exe_prefix = config.get("exe_prefix", "inversek2j_")
        executables_dir = config["executables_dir"]
        optimization = config.get("optimization_level", "-O")
        
        main_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_hash}_main.o")
        kernel_obj_file = os.path.join(executables_dir, f"{exe_prefix}{output_hash}_kernel.o")
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
            logging.error(f"[{variant_id}] Erro compilação Main: {e.stderr.strip()}")
            status_monitor.update_status(variant_id, "Erro Compilação Main")
            return False, None
        
        # 2. Compilar Kernel
        compile_kernel_cmd = [
            "riscv32-unknown-elf-g++", "-march=rv32imafdcv", optimization, *include_flags,
            "-c", kernel_cpp, "-o", kernel_obj_file, "-lm"
        ]
        try:
            subprocess.run(compile_kernel_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"[{variant_id}] Erro compilação Kernel: {e.stderr.strip()}")
            status_monitor.update_status(variant_id, "Erro Compilação Kernel")
            return False, None
        
        # 3. Linkar
        link_cmd = [
            "riscv32-unknown-elf-g++", "-march=rv32imafdcv",
            main_obj_file, kernel_obj_file, "-o", exe_file, "-lm"
        ]
        try:
            subprocess.run(link_cmd, check=True, capture_output=True, text=True)
            logging.info(f"[{variant_id}] Linkado com sucesso -> {os.path.basename(exe_file)}")
        except subprocess.CalledProcessError as e:
            logging.error(f"[{variant_id}] Erro linkagem: {e.stderr.strip()}")
            status_monitor.update_status(variant_id, "Erro Linkagem")
            return False, None
        
        os.chmod(exe_file, 0o755)
        status_monitor.update_status(variant_id, "Compilado")
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
        
        is_original = os.path.abspath(variant_file) == os.path.abspath(config["kinematics_source_file"])
        variant_id = "original" if is_original else short_hash(variant_hash)
        
        exe_prefix = config.get("exe_prefix", "inversek2j_")
        
        spike_output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['output_suffix']}")
        time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
        prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
        spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
        prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
        
        main_to_compile = config["inversek2j_main_file"]
        kernel_to_compile = variant_file
        
        compiled_ok, exe_file = self._compile_kinematics_variant(
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
                    config["kinematics_source_file"],
                    resume_context["variant_hash"],
                    config
                )
            except Exception:
                pass
            
            status_monitor.update_status(resume_context["variant_id"], "Concluída")
            return True
        finally:
            self.cleanup_variant_files(resume_context["variant_hash"], config)
    
    def save_modified_lines_txt(self, variant_file: str, original_file: str, variant_hash: str, config: Dict) -> Optional[str]:
        """Versão customizada para InverseK2J usando kernel_source_file."""
        try:
            with open(original_file, 'r') as f_o, open(variant_file, 'r') as f_v:
                o_lines, v_lines = f_o.readlines(), f_v.readlines()
            
            modified_indices = [i for i, (l1, l2) in enumerate(zip(o_lines, v_lines)) if l1 != l2]
            
            linhas_dir = config.get("linhas_modificadas_dir", "storage/linhas_modificadas")
            os.makedirs(linhas_dir, exist_ok=True)
            txt_path = os.path.join(linhas_dir, f"linhas_{variant_hash}.txt")
            
            with open(txt_path, 'w') as f:
                for idx in modified_indices:
                    f.write(f"{idx}\n")
            
            return txt_path
        except Exception as e:
            logging.error(f"Erro ao salvar linhas modificadas: {e}")
            return None
    
    def calculate_custom_error(self, reference_file: str, variant_file: str) -> Optional[float]:
        """Calcula ARE (Average Relative Error) para InverseK2J."""
        try:
            def read_floats(filepath):
                with open(filepath, 'r') as f:
                    content = f.read().replace('\n', ' ').split()
                    return [float(x) for x in content if x.strip()]
            
            ref_data = read_floats(reference_file)
            var_data = read_floats(variant_file)
            
            if not ref_data:
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
                are = sum_abs_diff / sum_abs_ref
                logging.info(f"[INVERSEK2J Metric] ARE: {are:.8f}")
                return are
            return 0.0
            
        except Exception as e:
            logging.error(f"[INVERSEK2J Error] Falha ao calcular ARE: {e}")
            return None


# Instância global para compatibilidade com run.py
app = InverseK2JApp()

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
        config.get("kinematics_source_file"),
        variant_hash,
        config
    )
