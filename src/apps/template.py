"""
Template para novas aplicações do PaCA

Este template demonstra como criar uma nova aplicação herdando de BaseApp.
Para criar um novo app:

1. Copie este arquivo para src/apps/novo_app.py
2. Renomeie a classe para NovoApp e altere o CONFIG
3. Implemente os métodos abstratos
4. Adicione ao dicionário AVAILABLE_APPS em run.py
"""

import os
import glob
import logging
import subprocess
from typing import Dict, List, Tuple, Optional, Any

from src.apps.base import BaseApp


class NovoApp(BaseApp):
    """
    Aplicação modelo herdando de BaseApp.
    
    CONFIGURAÇÃO NECESSÁRIA:
    Preencha o dicionário CONFIG abaixo com os caminhos corretos:
    """
    
    CONFIG: Dict[str, Any] = {
        # === ARQUIVOS OBRIGATÓRIOS ===
        "original_file": "data/applications/novo_app/src/main.cpp",
        
        # Arquivo que receberá as mutações (pode ser igual a original_file)
        "input_file_for_variants": "data/applications/novo_app/src/main.cpp",
        
        # Entrada para simulação
        "train_data_input": "data/applications/novo_app/train.data/input/1.dat",
        
        # === PADRÕES DE ARQUIVOS ===
        "source_pattern": "main_*.cpp",  # Padrão para variantes geradas
        "exe_prefix": "novoapp_",         # Prefixo para executáveis
        
        # === SUFIXOS ===
        "output_suffix": ".data",
        "time_suffix": ".time",
        "prof5_suffix": ".prof5",
        
        # === MAPA DE OPERADORES ===
        # Mapeia operadores para macros aproximadas
        "operations_map": {
            '*': 'FMULX',
            '+': 'FADDX',
            '-': 'FSUBX'
        },
        
        # === COMPILAÇÃO ===
        "include_dir": "data/applications/novo_app/src",
        "optimization_level": "-O",
        
        # === MODELO DE ENERGIA ===
        "prof5_model": "data/models/APPROX_1.json",
        
        # === OPCIONAIS ===
        # Arquivos extras para linkagem (se houver)
        # "extra_files": ["file1.cpp", "file2.cpp"],
        
        # Estratégia de geração (para gera_variantes.py)
        # "strategy": "all",  # ou "one_hot"
        # "max_variantes": 1000,
    }
    
    # Chaves obrigatórias para validação
    REQUIRED_CONFIG_KEYS = [
        "original_file",
        "input_file_for_variants",
        "operations_map",
        "exe_prefix",
        "train_data_input",
    ]
    
    def __init__(self):
        """Inicializa o app validando a configuração."""
        super().__init__()
    
    def get_config(self) -> Dict[str, Any]:
        """Retorna a configuração completa do app."""
        return self.CONFIG
    
    def prepare_environment(self, base_config: Dict) -> bool:
        """
        Prepara o ambiente para execução.
        Copia arquivos necessários para o diretório de trabalho.
        """
        config = self._merge_config(base_config)
        
        # Copia approx.h para o diretório de input
        approx_source = config.get("approx_file", "data/reference/approx.h")
        os.makedirs(config["input_dir"], exist_ok=True)
        
        from src.utils.file_utils import copy_file
        return copy_file(approx_source, config["input_dir"])
    
    def generate_variants(self, base_config: Dict) -> bool:
        """
        Gera todas as variantes do código usando gera_variantes.py.
        """
        config = self._merge_config(base_config)
        
        import sys
        output_dir = os.path.abspath(config.get("input_dir", "storage/variantes"))
        input_path = os.path.abspath(config["input_file_for_variants"])
        executados = os.path.abspath(config.get("executed_variants_file", ""))
        
        os.makedirs(output_dir, exist_ok=True)
        
        cmd = [
            sys.executable, "src/gera_variantes.py",
            "--input", input_path,
            "--output", output_dir,
            "--strategy", config.get("strategy", "all")
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
            logging.error(f"Erro ao gerar variantes: {e}")
            return False
    
    def find_variants_to_simulate(self, base_config: Dict) -> Tuple[List[Tuple[str, str]], Dict]:
        """
        Identifica variantes que precisam ser simuladas.
        Retorna: (lista de tuplas [arquivo, hash], mapeamento físico->lógico)
        """
        config = self._merge_config(base_config)
        
        from src.database.variant_tracker import load_executed_variants
        from src.hash_utils import gerar_hash_codigo_logico
        from src.code_parser import parse_code
        
        executed_variants = load_executed_variants(config["executed_variants_file"])
        variants_to_simulate = []
        
        # Parse do arquivo original
        _, __, physical_to_logical = parse_code(config["original_file"])
        
        # Hash do original
        with open(config["original_file"], "r") as f:
            original_hash = gerar_hash_codigo_logico(f.readlines(), physical_to_logical)
        
        # Adiciona original se não foi executado
        if original_hash not in executed_variants:
            variants_to_simulate.append((config["original_file"], original_hash))
        
        # Busca variantes geradas
        pattern = os.path.join(config["input_dir"], config["source_pattern"])
        if not glob.glob(pattern):
            self.generate_variants(base_config)
        
        for file_path in glob.glob(pattern):
            if os.path.abspath(file_path) == os.path.abspath(config["original_file"]):
                continue
            
            with open(file_path, "r") as f:
                variant_hash = gerar_hash_codigo_logico(f.readlines(), physical_to_logical)
            
            if variant_hash not in executed_variants:
                variants_to_simulate.append((file_path, variant_hash))
        
        return variants_to_simulate, physical_to_logical
    
    def simulate_variant(
        self,
        variant_file: str,
        variant_hash: str,
        base_config: Dict,
        status_monitor,
        only_spike: bool = False
    ) -> Tuple[Optional[str], Optional[Dict]]:
        """
        Executa simulação completa de uma variante.
        Retorna: (caminho_output, contexto) ou (None, None)
        """
        config = self._merge_config(base_config)
        
        from src.utils.file_utils import short_hash
        from src.execution.simulation import run_spike_simulation
        
        variant_id = "original" if variant_file == config["original_file"] else short_hash(variant_hash)
        
        # Define nomes de arquivos
        exe_prefix = config["exe_prefix"]
        exe_file = os.path.join(config["executables_dir"], f"{exe_prefix}{variant_hash}")
        output_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['output_suffix']}")
        time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['time_suffix']}")
        prof5_time_file = os.path.join(config["outputs_dir"], f"{exe_prefix}{variant_hash}{config['prof5_suffix']}")
        spike_log_file = os.path.join(config["logs_dir"], f"{exe_prefix}{variant_hash}.log")
        prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
        
        # 1. Compilação (implemente conforme necessidade)
        # Você pode usar self._compile_simple() ou implementar compilação customizada
        status_monitor.update_status(variant_id, "Compilando")
        
        include_dir = config.get("include_dir", os.path.dirname(config.get("original_file", ".")))
        
        compile_cmd = [
            "riscv32-unknown-elf-g++",
            "-march=rv32imafdcv",
            config.get("optimization_level", "-O"),
            "-I", config["input_dir"],
            "-I", include_dir,
            variant_file,
            "-o", exe_file,
            "-lm"
        ]
        
        import subprocess
        try:
            subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            logging.error(f"Erro compilação: {e.stderr}")
            return (None, None)
        
        # 2. Simulação Spike
        sim_time = run_spike_simulation(
            exe_file, config["train_data_input"], output_file,
            spike_log_file, variant_id, status_monitor
        )
        if sim_time is None:
            return (None, None)
        
        # Salva tempo
        with open(time_file, 'w') as tf:
            tf.write(f"{sim_time}\n")
        
        # Contexto para profiling
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
            return output_file, resume_context
        
        # 4. Profiling
        success = self._run_profiling_stage(resume_context, base_config, status_monitor)
        
        if success:
            return output_file, None
        else:
            return None, None
    
    def _run_profiling_stage(self, resume_context: Dict, base_config: Dict, status_monitor) -> bool:
        """Executa profiling (Prof5Fake)."""
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
            
            status_monitor.update_status(resume_context["variant_id"], "Concluída")
            return True
        finally:
            self.cleanup_variant_files(resume_context["variant_hash"], config)
    
    # =========================================================================
    # MÉTODOS OPCIONAIS (SOBRESCREVER SE NECESSÁRIO)
    # =========================================================================
    
    def calculate_custom_error(self, reference_file: str, variant_file: str) -> Optional[float]:
        """
        Calcula erro entre referência e variante.
        Sobrescreva para implementar cálculo específico do app.
        
        Por padrão, retorna None (usa error_analyzer genérico).
        """
        return None
    
    def cleanup_variant_files(self, variant_hash: str, config: Dict) -> None:
        """
        Remove arquivos temporários.
        Sobrescreva se o app precisar de limpeza especial.
        """
        exe_prefix = config.get("exe_prefix", "app_")
        logs_dir = config.get("logs_dir", "storage/logs")
        
        spike_log = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
        
        if os.path.exists(spike_log):
            try:
                os.remove(spike_log)
            except OSError:
                pass


# =========================================================================
# COMPATIBILIDADE COM run.py
# =========================================================================

# Instância global para compatibilidade com run.py
app = NovoApp()

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
