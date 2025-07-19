import os
from config_base import BASE_CONFIG

# Configurações do projeto
CONFIG = {
    **BASE_CONFIG,  # Inclui todas as configurações base
    "input_file": "axbench/applications/inversek2j/src/kinematics.cpp",
    "output_folder": "storage/variantes",  # Modificado aqui
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'}
}


def get_config():
    return CONFIG.copy()

def update_config(new_config):
    global CONFIG
    CONFIG.update(new_config)