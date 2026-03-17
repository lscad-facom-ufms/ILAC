"""
BaseApp - Classe base abstrata para todos os aplicações do PaCA

Esta classe define a interface padrão e métodos utilitários compartilhados
por todas as aplicações (FFT, Kmeans, Sobel, JMeint, InverseK2J, BlackScholes).
"""

import os
import glob
import logging
import subprocess
import json
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Any

# Imports do projeto
from src.code_parser import parse_code
from src.hash_utils import gerar_hash_codigo_logico
from src.database.variant_tracker import load_executed_variants
from src.utils.file_utils import short_hash, copy_file
from src.execution.compilation import generate_dump
from src.execution.simulation import run_spike_simulation
from src.transformations import apply_transformation
from src.utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia


class BaseApp(ABC):
    """
    Classe abstrata base para aplicações do PaCA.
    
    Attributes:
        CONFIG: Dicionário de configuração específico do app (deve ser sobrescrito)
        REQUIRED_CONFIG_KEYS: Lista de chaves obrigatórias na configuração
    """
    
    CONFIG: Dict[str, Any] = {}
    REQUIRED_CONFIG_KEYS: List[str] = [
        "original_file",
        "input_file_for_variants", 
        "operations_map",
        "exe_prefix",
        "train_data_input",
    ]
    
    def __init__(self):
        self._validate_config()
    
    def _validate_config(self) -> None:
        missing = [key for key in self.REQUIRED_CONFIG_KEYS if key not in self.CONFIG]
        if missing:
            raise ValueError(
                f"Configuração ausente para {self.__class__.__name__}: {missing}"
            )
    
    @abstractmethod
    def get_config(self) -> Dict[str, Any]:
        pass
    
    @abstractmethod
    def prepare_environment(self, base_config: Dict) -> bool:
        pass
    
    @abstractmethod
    def generate_variants(self, base_config: Dict) -> bool:
        pass
    
    @abstractmethod
    def find_variants_to_simulate(self, base_config: Dict) -> Tuple[List[Tuple[str, str]], Dict]:
        pass
    
    @abstractmethod
    def simulate_variant(
        self, 
        variant_file: str, 
        variant_hash: str, 
        base_config: Dict,
        status_monitor,
        only_spike: bool = False
    ) -> Tuple[Optional[str], Optional[Any]]:
        pass
    
    def cleanup_variant_files(self, variant_hash: str, config: Dict) -> None:
        exe_prefix = config.get("exe_prefix", "app_")
        logs_dir = config.get("logs_dir", "storage/logs")
        
        spike_log = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
        
        if os.path.exists(spike_log):
            try:
                os.remove(spike_log)
            except OSError:
                pass
    
    def get_pruning_config(self, base_config: Dict) -> Dict:
        config = self._merge_config(base_config)
        source_file = config["input_file_for_variants"]
        original_lines, modifiable_lines, physical_to_logical = parse_code(source_file)
        
        return {
            "source_file": source_file,
            "original_lines": original_lines,
            "modifiable_lines": modifiable_lines,
            "physical_to_logical": physical_to_logical,
            "app_specific_config": config
        }
    
    def generate_specific_variant(
        self, 
        original_lines: List[str], 
        physical_to_logical: Dict[int, int],
        modified_line_indices: List[int],
        config: Dict
    ) -> Tuple[str, str]:
        modified_content = list(original_lines)
        
        for idx in modified_line_indices:
            orig = modified_content[idx]
            transformed = apply_transformation(orig, config["operations_map"])
            if not transformed.endswith("\n") and orig.endswith("\n"):
                transformed += "\n"
            modified_content[idx] = transformed
        
        variant_hash = gerar_hash_codigo_logico(modified_content, physical_to_logical)
        
        variant_dir = config.get("input_dir", "storage/variantes")
        base_name = os.path.splitext(os.path.basename(config["input_file_for_variants"]))[0]
        ext = os.path.splitext(config["input_file_for_variants"])[1]
        variant_path = os.path.join(variant_dir, f"{base_name}_{variant_hash}{ext}")
        
        os.makedirs(variant_dir, exist_ok=True)
        with open(variant_path, 'w', encoding='utf-8') as f:
            f.writelines(modified_content)
        
        return variant_path, variant_hash
    
    def calculate_custom_error(self, reference_file: str, variant_file: str) -> Optional[float]:
        return None
    
    def save_modified_lines_txt(
        self, 
        variant_file: str, 
        original_file: str, 
        variant_hash: str, 
        config: Dict
    ) -> Optional[str]:
        try:
            with open(original_file, 'r') as f_o, open(variant_file, 'r') as f_v:
                o_lines, v_lines = f_o.readlines(), f_v.readlines()
            
            _, __, p2l = parse_code(original_file)
            modified_indices = [
                i for i, (l1, l2) in enumerate(zip(o_lines, v_lines)) 
                if l1 != l2
            ]
            
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
    
    def _run_prof5_fake(
        self,
        spike_log_file: str,
        prof5_model: str,
        prof5_time_file: str,
        prof5_report_path: str,
        variant_id: str,
        status_monitor
    ) -> Optional[float]:
        try:
            status_monitor.update_status(variant_id, "Executando Prof5Fake")
            
            if not os.path.exists(spike_log_file):
                logging.error(f"[{variant_id}] Log Spike não encontrado")
                return None
            
            instrucoes = contar_instrucoes_log(spike_log_file)
            if not instrucoes:
                logging.error(f"[{variant_id}] Falha ao contar instruções")
                return None
            
            if not prof5_model or not os.path.exists(prof5_model):
                prof5_model = "data/models/APPROX_1.json"
                if not os.path.exists(prof5_model):
                    logging.error(f"[{variant_id}] Modelo não encontrado")
                    return None
            
            resultados = avaliar_modelo_energia(instrucoes, prof5_model)
            if not resultados:
                return None
            
            os.makedirs(os.path.dirname(prof5_report_path), exist_ok=True)
            with open(prof5_report_path, 'w') as f:
                json.dump(resultados, f, indent=2)
            
            latency_ms = resultados["summary"]["latency_ms"]
            with open(prof5_time_file, 'w') as f:
                f.write(f"{latency_ms}\n")
            
            status_monitor.update_status(variant_id, "Prof5Fake Concluído")
            return latency_ms
            
        except Exception as e:
            logging.error(f"[{variant_id}] Erro no Prof5Fake: {e}")
            return None
    
    def _compile_simple(
        self,
        variant_file: str,
        variant_hash: str,
        config: Dict,
        status_monitor,
        extra_files: Optional[List[str]] = None
    ) -> Tuple[bool, Optional[str]]:
        if extra_files is None:
            extra_files = []
        is_original = os.path.abspath(variant_file) == os.path.abspath(config.get("original_file", ""))
        variant_id = "original" if is_original else short_hash(variant_hash)
        
        status_monitor.update_status(variant_id, "Compilando")
        
        exe_prefix = config.get("exe_prefix", "app_")
        exe_file = os.path.join(config["executables_dir"], f"{exe_prefix}{variant_hash}")
        
        include_dir = config.get("include_dir", os.path.dirname(config.get("original_file", ".")))
        
        compile_cmd = [
            "riscv32-unknown-elf-g++",
            "-march=rv32imafdcv",
            config.get("optimization_level", "-O"),
            "-I", config["input_dir"],
            "-I", include_dir,
        ]
        
        if extra_files:
            compile_cmd.extend(extra_files)
        
        compile_cmd.extend([variant_file, "-o", exe_file, "-lm"])
        
        try:
            subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
            os.chmod(exe_file, 0o755)
            return True, exe_file
        except subprocess.CalledProcessError as e:
            logging.error(f"[{variant_id}] Erro compilação: {e.stderr}")
            status_monitor.update_status(variant_id, "Erro Compilação")
            return False, None
    
    def _merge_config(self, base_config: Dict) -> Dict:
        return {**base_config, **self.CONFIG}
