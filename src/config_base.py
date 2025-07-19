"""Configurações base compartilhadas entre todas as aplicações"""

# Configuração base para execução (comum a todas as aplicações)
BASE_CONFIG = {
    # Diretórios
    "executables_dir": "storage/executable",
    "outputs_dir": "storage/output",
    "logs_dir": "storage/logs",  
    "input_dir": "storage/variantes",
    "prof5_results_dir": "storage/prof5_results",
    "dump_dir": "storage/dump",
    
    # Arquivos de configuração
    "approx_file": "data/reference/approx.h",
    "executed_variants_file": "data/reference/executados.txt",
    "failed_variants_file": "data/reference/falhas.txt",
    
    # Configurações do Prof5
    "prof5_model": "prof5/models/APPROX_1.json"
}