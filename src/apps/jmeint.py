import os
import glob
import logging
import subprocess
import json
import sys

from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico
from database.variant_tracker import load_executed_variants
from utils.file_utils import short_hash, copy_file, TempFiles
from execution.compilation import generate_dump
from execution.simulation import run_spike_simulation, save_modified_lines
from transformations import apply_transformation
from utils.prof5fake import contar_instrucoes_log, avaliar_modelo_energia

# Configurações específicas para a aplicação JMEINT
JMEINT_CONFIG = {
    # Arquivos específicos da aplicação
    "jmeint_main_file": "data/applications/jmeint/src/jmeint.cpp", # Renomeado para clareza
    "tritri_source_file": "data/applications/jmeint/src/tritri.cpp", # Arquivo original que pode ter variantes
    "train_data_input": "data/applications/jmeint/train.data/input/jmeint_500.data",

    # Padrões de arquivos para variantes de tritri.cpp
    "source_pattern": "tritri_*.cpp", # Padrão para encontrar variantes de tritri.cpp
    "exe_prefix": "jmeint_",
    "output_suffix": ".data",
    "time_suffix": ".time",
    "prof5_suffix": ".prof5",

    # Parâmetros de geração de variantes (para tritri.cpp)
    "input_file_for_variants": "data/applications/jmeint/src/tritri.cpp",
    "operations_map": {'*': 'FMULX', '+': 'FADDX', '-': 'FSUBX'},

    # Parâmetros específicos de compilação
    "include_dir": "data/applications/jmeint/src",
    "optimization_level": "-O",
}

def cleanup_variant_files(variant_hash, config):
    """Não remove logs do Spike; apenas remove arquivos de dump opcionais."""
    exe_prefix = config["exe_prefix"]
    logs_dir = config["logs_dir"]
    dump_dir = config["dump_dir"]
    spike_log_file = os.path.join(logs_dir, f"{exe_prefix}{variant_hash}.log")
    dump_file = os.path.join(dump_dir, f"dump_{variant_hash}.txt")
    
    # Preservar logs do Spike para posterior análise
    if os.path.exists(spike_log_file):
        logging.debug(f"Preservando log do Spike: {spike_log_file}")

    # Remover apenas o arquivo de dump (se existir)
    if os.path.exists(dump_file):
        try:
            os.remove(dump_file)
            logging.debug(f"Dump removido: {dump_file}")
        except OSError as e:
            logging.warning(f"Não foi possível remover o dump {dump_file}: {e}")

def run_prof5_fake(spike_log_file, prof5_model, prof5_time_file, prof5_report_path, variant_id, status_monitor):
    """
    Executa o prof5fake (substituto do Prof5) para análise de energia e performance.
    
    Args:
        spike_log_file: Arquivo de log do Spike
        prof5_model: Caminho para o modelo de energia JSON
        prof5_time_file: Arquivo onde salvar o tempo de execução
        prof5_report_path: Caminho para o relatório detalhado
        variant_id: ID da variante para logs
        status_monitor: Monitor de status
        
    Returns:
        Tempo de execução ou None se falhar
    """
    try:
        status_monitor.update_status(variant_id, "Executando Prof5Fake")
        
        # Verifica se o arquivo de log existe
        if not os.path.exists(spike_log_file):
            logging.error(f"[Variante {variant_id}] Arquivo de log do Spike não encontrado: {spike_log_file}")
            return None
            
        # Verifica se o modelo existe
        if not os.path.exists(prof5_model):
            logging.error(f"[Variante {variant_id}] Modelo Prof5 não encontrado: {prof5_model}")
            return None
        
        logging.info(f"[Variante {variant_id}] Contando instruções no log do Spike...")
        
        # Etapa 1: Contar instruções no log
        instrucoes_dict = contar_instrucoes_log(spike_log_file)
        
        if not instrucoes_dict:
            logging.error(f"[Variante {variant_id}] Nenhuma instrução encontrada no log")
            return None
            
        logging.info(f"[Variante {variant_id}] Aplicando modelo de energia...")
        
        # Etapa 2: Avaliar modelo de energia
        resultados_energia = avaliar_modelo_energia(instrucoes_dict, prof5_model)
        
        if not resultados_energia:
            logging.error(f"[Variante {variant_id}] Falha na avaliação do modelo de energia")
            return None
            
        # Etapa 3: Salvar resultados
        with open(prof5_report_path, 'w') as f:
            json.dump(resultados_energia, f, indent=2, sort_keys=True)
        os.chmod(prof5_report_path, 0o666)
        
        # Extrair tempo de latência (em ms) dos resultados
        latency_ms = resultados_energia["summary"]["latency_ms"]
        
        # Salvar tempo no formato compatível
        with open(prof5_time_file, 'w') as f:
            f.write(f"{latency_ms}\n")
        os.chmod(prof5_time_file, 0o666)
        
        logging.info(f"[Variante {variant_id}] Prof5Fake concluído - Latência: {latency_ms:.2f} ms")
        status_monitor.update_status(variant_id, "Prof5Fake Concluído")
        
        return latency_ms
        
    except Exception as e:
        logging.error(f"[Variante {variant_id}] Erro no Prof5Fake: {e}", exc_info=True)
        status_monitor.update_status(variant_id, "Erro Prof5Fake")
        return None

def get_pruning_config(base_config):
    """Retorna a configuração necessária para o algoritmo de poda de árvore."""
    config = {**base_config, **JMEINT_CONFIG}
    source_file = config["input_file_for_variants"]
    original_lines, modifiable_lines, physical_to_logical = parse_code(source_file)
    return {
        "source_file": source_file,
        "original_lines": original_lines,
        "modifiable_lines": modifiable_lines,
        "physical_to_logical": physical_to_logical,
        "app_specific_config": config
    }

def generate_specific_variant(original_lines, physical_to_logical, modified_line_indices, config):
    """Gera um único arquivo de variante para tritri.cpp com base em um conjunto específico de linhas modificadas."""
    modified_lines_content = list(original_lines)
    for idx in modified_line_indices:
        modified_lines_content[idx] = apply_transformation(modified_lines_content[idx], config["operations_map"])

    variant_hash = gerar_hash_codigo_logico(modified_lines_content, physical_to_logical)
    
    variant_dir = config.get("input_dir", "storage/variantes")
    base_name, ext = os.path.splitext(os.path.basename(config["input_file_for_variants"]))
    variant_filepath = os.path.join(variant_dir, f"{base_name}_{variant_hash}{ext}")

    with open(variant_filepath, 'w') as f:
        f.writelines(modified_lines_content)
        
    return variant_filepath, variant_hash

def prepare_environment(base_config):
    """Prepara o ambiente específico para a aplicação JMEINT"""
    config = {**base_config, **JMEINT_CONFIG}
    approx_source = config.get("approx_file", "data/reference/approx.h")
    return copy_file(approx_source, config["input_dir"])

def generate_variants(base_config):
    """Gera variantes específicas para tritri.cpp dentro do contexto de JMEINT"""
    import subprocess
    import os
    import sys
    
    config = {**base_config, **JMEINT_CONFIG}
    print(f"DEBUG: Gerando em: {config['input_dir']}")
    print(f"DEBUG: Arquivo executados: {config['executed_variants_file']}")
    
    # VERIFICAR SE OS PARÂMETROS ESTÃO CORRETOS
    print(f"DEBUG: Parâmetros que serão passados:")
    print(f"  --input: {config['input_file_for_variants']}")
    print(f"  --output: {config['input_dir']}")
    print(f"  --executados: {config['executed_variants_file']}")
    
    try:
        # Usar caminhos ABSOLUTOS para garantir que funcionem
        input_path = os.path.abspath(config["input_file_for_variants"])
        output_path = os.path.abspath(config["input_dir"])
        executados_path = os.path.abspath(config["executed_variants_file"])
        
        # Use o mesmo interpretador Python e rode a partir do root do projeto
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        # project_root já aponta para .../ILAC/src — não adicionar outro "src" senão vira .../src/src
        gera_path = os.path.join(project_root, "gera_variantes.py")
        cmd = [
            sys.executable, gera_path,
            "--input", input_path,
            "--output", output_path,
            "--executados", executados_path
        ]
        print(f"DEBUG: Executando: {' '.join(cmd)} (cwd={project_root})")
        result = subprocess.run(cmd, cwd=project_root, capture_output=True, text=True)
        
        print(f"DEBUG: Return code: {result.returncode}")
        if result.stdout:
            print(f"DEBUG: STDOUT: {result.stdout[:500]}...")  # Primeiros 500 chars
        if result.stderr:
            print(f"DEBUG: STDERR: {result.stderr}")
        
        if result.returncode == 0:
            # Verificar se as variantes foram geradas NO LOCAL CORRETO
            import glob
            pattern = os.path.join(config["input_dir"], "tritri_*.cpp")
            generated_files = glob.glob(pattern)
            print(f"DEBUG: {len(generated_files)} variantes encontradas no workspace: {config['input_dir']}")
            
            if len(generated_files) > 0:
                print(f"DEBUG: Primeiras variantes encontradas:")
                for f in generated_files[:3]:
                    print(f"  - {f}")
                return True
            else:
                print(f"ERRO: Nenhuma variante encontrada no workspace mesmo com sucesso!")
                return False
        else:
            print(f"ERRO: Comando falhou com código {result.returncode}")
            return False
            
    except Exception as e:
        print(f"ERRO na geração: {e}")
        import traceback
        traceback.print_exc()
        return False

def find_variants_to_simulate(base_config):
    """Identifica as combinações de JMEINT (com variantes de tritri.cpp) que precisam ser simuladas."""
    config = {**base_config, **JMEINT_CONFIG}
    executed_variants = load_executed_variants(config["executed_variants_file"])
    variants_to_simulate = []

    # Buscar por variantes de tritri.cpp NO DIRETÓRIO DO WORKSPACE
    variant_pattern = os.path.join(config["input_dir"], config["source_pattern"])
    logging.info(f"Buscando variantes de tritri.cpp em: {variant_pattern}")
    
    # DEBUG: Verificar se o diretório existe e tem arquivos
    if os.path.exists(config["input_dir"]):
        all_files = os.listdir(config["input_dir"])
        logging.info(f"DEBUG: {len(all_files)} arquivos encontrados no workspace: {config['input_dir']}")
    else:
        logging.warning(f"DEBUG: Diretório do workspace não existe: {config['input_dir']}")
    
    # Mapa lógico do arquivo tritri.cpp original (base para hashes das variantes)
    tritri_original_path = config["tritri_source_file"]
    with open(tritri_original_path, "r") as f:
        tritri_original_lines = f.readlines()
    _, __, tritri_original_physical_to_logical = parse_code(tritri_original_path)
    
    # Hash da versão original de tritri.cpp
    tritri_original_hash = gerar_hash_codigo_logico(tritri_original_lines, tritri_original_physical_to_logical)

    # Adicionar a simulação da versão totalmente original (jmeint.cpp original + tritri.cpp original)
    # O hash identificador será o do tritri.cpp original
    if tritri_original_hash not in executed_variants:
        variants_to_simulate.append((tritri_original_path, tritri_original_hash))
        logging.info(f"Versão original de JMEINT (jmeint.cpp + {os.path.basename(tritri_original_path)}) será simulada (hash: {short_hash(tritri_original_hash)})")

    # Buscar variantes geradas no diretório de input (tritri_*.cpp)
    import glob
    variant_files = glob.glob(variant_pattern)

    for vf in variant_files:
        # Ignorar o arquivo original caso esteja presente com outro nome
        if os.path.abspath(vf) == os.path.abspath(tritri_original_path):
            continue

        try:
            with open(vf, "r") as f:
                vf_lines = f.readlines()
        except Exception as e:
            logging.warning(f"Não foi possível ler variante {vf}: {e}")
            continue

        # Gerar mapa físico->lógico para a variante com base no arquivo original
        # (assume-se que a estrutura de linhas é compatível)
        variant_hash = gerar_hash_codigo_logico(vf_lines, tritri_original_physical_to_logical)

        if variant_hash in executed_variants:
            logging.debug(f"Variante já executada (hash {short_hash(variant_hash)}): {vf}")
            continue

        logging.info(f"Variante detectada para simulação: {vf} (hash: {short_hash(variant_hash)})")
        variants_to_simulate.append((vf, variant_hash))

    return variants_to_simulate

def simulate_variant(source_file, variant_hash, execution_config, status_monitor, only_spike=False):
    """
    Simula uma variante (implementação mínima).
    Retorna (caminho_do_arquivo_de_tempo, None).

    Observação: este stub cria um arquivo de tempo placeholder no diretório de outputs
    para satisfazer a chamada em run.py. Substitua por execução real (compilação + Spike)
    quando necessário.
    """
    config = {**execution_config, **JMEINT_CONFIG}
    outputs_dir = config.get("outputs_dir", config.get("output_dir", os.path.join("storage", "outputs")))
    exe_prefix = config.get("exe_prefix", "jmeint_")
    time_suffix = config.get("time_suffix", ".time")

    try:
        os.makedirs(outputs_dir, exist_ok=True)
    except Exception:
        logging.warning(f"Não foi possível criar diretório de outputs: {outputs_dir}", exc_info=True)

    time_file = os.path.join(outputs_dir, f"{exe_prefix}{variant_hash}{time_suffix}")

    try:
        status_monitor.update_status(variant_hash, "Iniciando simulação (stub)")
    except Exception:
        logging.debug("status_monitor sem update_status disponível", exc_info=True)

    # Se já existe, apenas retorna; caso contrário, cria um placeholder com tempo 0.0
    if not os.path.exists(time_file):
        try:
            with open(time_file, "w") as f:
                f.write("0.0\n")
            try:
                os.chmod(time_file, 0o666)
            except Exception:
                pass
        except Exception as e:
            logging.error(f"Falha ao criar arquivo de tempo placeholder {time_file}: {e}", exc_info=True)

    try:
        status_monitor.update_status(variant_hash, "Simulação (stub) concluída")
    except Exception:
        pass

    # Retorna o path do .time como "reference_output_path" esperado por run.py
    return time_file, None