import os
import glob
import logging
from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import compile_variant, generate_dump
from execution.simulation import run_spike_simulation, run_prof5, save_modified_lines

# Configurações específicas para a aplicação
NOVA_APP_CONFIG = {
    # Arquivos específicos da aplicação
    "original_file": "caminho/para/arquivo/original.cpp",
    "objeto_principal": "caminho/para/objeto.o",
    "train_data_input": "caminho/para/dados/treinamento.data",
    
    # Padrões de arquivos
    "source_pattern": "padrao_*.cpp",
    "exe_prefix": "prefixo_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",
    
    # Parâmetros de geração de variantes
    "input_file_for_variants": "caminho/para/arquivo/original.cpp",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'}
}

def prepare_environment(base_config):
    """Prepara o ambiente específico para a aplicação"""
    # Exemplo: copiar arquivos de cabeçalho necessários
    source_file = base_config.get("approx_file", "data/reference/approx.h")
    return copy_file(source_file, base_config["input_dir"])

def generate_variants(base_config):
    """Gera variantes específicas para a aplicação"""
    from generator import generate_variants as gen_vars
    from code_parser import parse_code
    
    # Configura o gerador
    config = {**base_config, **NOVA_APP_CONFIG}
    
    print(f"Gerando variantes para a aplicação a partir de {config['input_file_for_variants']}")
    
    # Obtém os dados do arquivo original
    lines, modifiable_lines, physical_to_logical = parse_code(config["input_file_for_variants"])
    
    # Gera as variantes
    return gen_vars(
        lines, 
        modifiable_lines, 
        physical_to_logical, 
        config["operations_map"], 
        config["input_dir"], 
        os.path.basename(config["input_file_for_variants"]),
        config["executed_variants_file"]
    )

def find_variants_to_simulate(base_config):
    """Identifica as variantes que precisam ser simuladas"""
    # Configuração completa
    config = {**base_config, **NOVA_APP_CONFIG}
    
    # Carrega as variantes já executadas
    executed_variants = load_executed_variants(config["executed_variants_file"])
    
    # Lista para armazenar variantes a serem simuladas
    variants_to_simulate = []
    
    # Primeiro verifica o arquivo original
    with open(config["original_file"], "r") as f:
        original_lines = f.readlines()
    
    _, __, original_physical_to_logical = parse_code(config["original_file"])
    original_hash = gerar_hash_codigo_logico(original_lines, original_physical_to_logical)
    
    # Adiciona o arquivo original à lista se ainda não tiver sido executado
    if original_hash not in executed_variants:
        variants_to_simulate.append((config["original_file"], original_hash))
        print(f"Versão original será simulada (hash: {short_hash(original_hash)})")
    else:
        print(f"Versão original já foi executada (hash: {short_hash(original_hash)})")
    
    # Processa cada arquivo de variante no diretório
    pattern = os.path.join(config["input_dir"], config["source_pattern"])
    for file in glob.glob(pattern):
        with open(file, "r") as f:
            variant_lines = f.readlines()
        
        variant_hash = gerar_hash_codigo_logico(variant_lines, original_physical_to_logical)
        
        if variant_hash not in executed_variants:
            variants_to_simulate.append((file, variant_hash))
            print(f"Variante {os.path.basename(file)} será simulada (hash: {short_hash(variant_hash)})")
        else:
            print(f"Variante {os.path.basename(file)} já foi executada (hash: {short_hash(variant_hash)})")
    
    return variants_to_simulate, original_physical_to_logical

def simulate_variant(variant_file, variant_hash, base_config, status_monitor):
    """Simula uma variante específica"""
    # Configuração completa
    config = {**base_config, **NOVA_APP_CONFIG}
    
    # Identificador para logs
    is_original = (variant_file == config["original_file"])
    variant_id = "original" if is_original else short_hash(variant_hash)
    
    # Definir nomes de arquivos
    exe_file = os.path.join(config["executables_dir"], f"{config['exe_prefix']}{variant_hash}")
    output_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['output_suffix']}")
    time_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['time_suffix']}")
    spike_log_file = os.path.join(config["logs_dir"], f"{config['exe_prefix']}{variant_hash}.log")
    prof5_time_file = os.path.join(config["outputs_dir"], f"{config['exe_prefix']}{variant_hash}{config['prof5_suffix']}")
    prof5_report_path = os.path.join(config["prof5_results_dir"], f"prof5_results_{variant_hash}.json")
    dump_file = os.path.join(config["dump_dir"], f"dump_{variant_hash}.txt")
    
    # Gerenciamento de arquivos temporários
    with TempFiles([spike_log_file, dump_file]):
        # Passo 1: Compilar a variante
        if not compile_variant(variant_file, variant_hash, config, status_monitor):
            return False
        
        # Passo 2: Gerar o dump
        if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
            return False
        
        # Passo 3: Executar a simulação com Spike
        sim_time = run_spike_simulation(
            exe_file, 
            config["train_data_input"], 
            output_file, 
            spike_log_file, 
            variant_id, 
            status_monitor
        )
        if sim_time is None:
            return False
        
        # Salva o tempo de simulação
        with open(time_file, 'w') as tf:
            tf.write(f"{sim_time}\n")
        os.chmod(time_file, 0o666)
        
        # Passo 4: Executar o Prof5
        prof5_time = run_prof5(
            exe_file, 
            spike_log_file, 
            dump_file, 
            config["prof5_model"],
            config["prof5_executable"],
            prof5_time_file,
            prof5_report_path,
            variant_id,
            status_monitor
        )
        if prof5_time is None:
            return False
        
        # Salvar as linhas modificadas para análise posterior
        save_modified_lines(variant_file, config["original_file"], variant_hash, config, parse_code)
    
    logging.info(f"[Variante {variant_id}] Simulação completa finalizada com sucesso!")
    status_monitor.update_status(variant_id, "Concluída")
    
    return True