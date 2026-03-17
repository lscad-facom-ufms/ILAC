#!/usr/bin/env python3

import os
import sys
import argparse
import glob
import inspect
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import deque
import logging
import threading
import json

# Adicionar diretório raiz ao path para imports absolutos
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Importações gerais
from src.config_base import BASE_CONFIG
from src.database.variant_tracker import add_executed_variant, add_failed_variant
from src.utils.logger import setup_logging, VariantStatusMonitor
from src.utils.file_utils import ensure_dirs, short_hash, generate_report, save_checkpoint, load_checkpoint
from src.hash_utils import gerar_hash_codigo_logico

# Importações para o modo de poda de árvore
from utils.pruning_tree import build_variant_tree, prune_branch, save_tree_to_file, save_tree_to_dot
from utils.error_analyzer import calculate_error

def get_cleanup_config(config):
    """
    Retorna a configuração correta para limpeza, independente do modo de execução.
    Resolve o problema de KeyError no modo Força Bruta.
    """
    if 'pruning_config' in config and 'app_specific_config' in config['pruning_config']:
        return {**config['base_config'], **config['pruning_config']['app_specific_config']}
    return config

def save_modified_lines_for_bruteforce(variant_file, original_file, variant_hash, app_module, config):
    """Compara uma variante com o original e salva os índices das linhas modificadas."""
    if not hasattr(app_module, 'save_modified_lines_txt'):
        return

    try:
        import inspect
        sig = inspect.signature(app_module.save_modified_lines_txt)
        params = list(sig.parameters)
        if len(params) == 4:
            # Apps novos que já calculam internamente (Kmeans, etc)
            app_module.save_modified_lines_txt(variant_file, original_file, variant_hash, config)
        else:
            # Apps que esperam receber a lista de índices (FFT, JMeint, etc)
            with open(variant_file, 'r') as f_variant, open(original_file, 'r') as f_original:
                variant_lines = f_variant.readlines()
                original_lines = f_original.readlines()
            
            # Garante que têm o mesmo tamanho para comparação linha a linha
            max_len = max(len(variant_lines), len(original_lines))
            variant_lines.extend([''] * (max_len - len(variant_lines)))
            original_lines.extend([''] * (max_len - len(original_lines)))
            
            # Identifica índices onde houve mudança
            modified_indices = [i for i, (line1, line2) in enumerate(zip(original_lines, variant_lines)) if line1 != line2]
            
            if modified_indices:
                app_module.save_modified_lines_txt(modified_indices, variant_hash, config)
            else:
                logging.warning(f"Nenhuma diferença encontrada entre variante {short_hash(variant_hash)} e original.")

    except FileNotFoundError:
        logging.warning(f"Arquivo original '{original_file}' ou da variante '{variant_file}' não encontrado para salvar linhas modificadas.")
    except Exception as e:
        logging.error(f"Falha ao salvar índices de linhas modificadas para hash {variant_hash}: {e}")

AVAILABLE_APPS = {
    "blackscholes": "apps.blackscholes",
    "inversek2j": "apps.inversek2j",
    "fft": "apps.fft",
    "jmeint": "apps.jmeint",
    "kmeans": "apps.kmeans",
    "sobel": "apps.sobel"
}

def create_execution_workspace(app_name, execution_mode, base_config):
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    workspace_name = f"{app_name}_{execution_mode}_{timestamp}"
    workspace_path = os.path.join("storage", "executions", workspace_name)
    
    execution_config = base_config.copy()
    execution_config.update({
        "workspace_path": workspace_path,
        "executables_dir": os.path.join(workspace_path, "executables"),
        "outputs_dir": os.path.join(workspace_path, "outputs"),
        "input_dir": os.path.join(workspace_path, "variants"),
        "logs_dir": os.path.join(workspace_path, "logs"),
        "prof5_results_dir": os.path.join(workspace_path, "prof5_results"),
        "dump_dir": os.path.join(workspace_path, "dumps"),
        "linhas_modificadas_dir": os.path.join(workspace_path, "linhas_modificadas"),
        "executed_variants_file": os.path.join(workspace_path, "executed_variants.json"),
        "failed_variants_file": os.path.join(workspace_path, "failed_variants.json"),
        "checkpoint_file": os.path.join(workspace_path, "checkpoint.json")
    })
    
    ensure_dirs(
        execution_config["executables_dir"],
        execution_config["outputs_dir"], 
        execution_config["input_dir"],
        execution_config["logs_dir"],
        execution_config["prof5_results_dir"],
        execution_config["dump_dir"],
        execution_config["linhas_modificadas_dir"]
    )
    
    execution_info = {
        "app_name": app_name,
        "execution_mode": execution_mode,
        "timestamp": timestamp,
        "start_time": datetime.now().isoformat(),
        "workspace_path": workspace_path,
        "base_storage": base_config.get("storage_root", "storage")
    }
    
    info_file = os.path.join(workspace_path, "execution_info.json")
    with open(info_file, 'w') as f:
        json.dump(execution_info, f, indent=2)
    
    logging.info(f"Workspace criado: {workspace_path}")
    return execution_config

def check_dependencies():
    import shutil
    tools = ["riscv32-unknown-elf-g++", "riscv32-unknown-elf-objdump", "spike"]
    missing = [tool for tool in tools if not shutil.which(tool)]
    if missing:
        logging.error(f"Ferramentas necessárias não encontradas: {', '.join(missing)}")
        return False
    return True

def setup_environment(app_name, execution_config):
    os.environ["PATH"] += ":/opt/riscv/bin"
    
    if app_name not in AVAILABLE_APPS:
        logging.error(f"Erro: Aplicação '{app_name}' não encontrada.")
        return False
    
    try:
        app_module = __import__(AVAILABLE_APPS[app_name], fromlist=[''])
    except ImportError as e:
        logging.error(f"Erro: Não foi possível importar o módulo '{AVAILABLE_APPS[app_name]}': {e}")
        return False
    
    logging.info("Gerando todas as variantes para esta execução...")
    app_module.generate_variants(execution_config)
    
    if not app_module.prepare_environment(execution_config):
        logging.error(f"Erro: Falha ao preparar ambiente para '{app_name}'")
        return False
    
    return app_module

def process_node(node, app_module, config, threshold, reference_output_path, status_monitor, db_lock, original_energy, alpha):
    """Processa um nó na árvore: simulação completa (Spike+Prof5), cálculo de erro e energia, e aplicação da heurística."""
    if node.status != 'PENDING':
        return node

    node.status = 'SIMULATING'
    cleanup_conf = get_cleanup_config(config)

    try:
        variant_filepath, variant_hash = app_module.generate_specific_variant(
            config['pruning_config']['original_lines'],
            config['pruning_config']['physical_to_logical'],
            node.modified_lines,
            config['pruning_config']['app_specific_config']
        )
        node.variant_hash = variant_hash
    except Exception as e:
        logging.error(f"Erro ao gerar variante específica para nó {node.name}: {e}")
        node.status = 'FAILED'
        return node

    try:
        variant_output_path, _ = app_module.simulate_variant(
            variant_filepath, variant_hash, config['base_config'], status_monitor, only_spike=False
        )
    except Exception as e:
        logging.error(f"Exceção durante a simulação unificada do nó {node.name}: {e}")
        variant_output_path = None

    if variant_output_path is None:
        node.status = 'FAILED'
        add_failed_variant(variant_hash, "simulation_failure", config['base_config']["failed_variants_file"], lock=db_lock)
        if hasattr(app_module, 'cleanup_variant_files'):
            app_module.cleanup_variant_files(variant_hash, cleanup_conf)
        prune_branch(node)
        return node

    error = None
    if hasattr(app_module, 'calculate_custom_error'):
        error = app_module.calculate_custom_error(reference_output_path, variant_output_path)
    else:
        accuracy_data = calculate_error(reference_output_path, variant_output_path)
        if accuracy_data is not None:
            try:
                if isinstance(accuracy_data, dict):
                    accuracy_val = float(accuracy_data.get('accuracy', list(accuracy_data.values())[0]))
                else:
                    accuracy_val = float(accuracy_data)
                error = 1.0 - accuracy_val
            except Exception as e:
                logging.error(f"Erro ao converter acurácia para erro: {e}")
                error = None

    if error is None:
        node.status = 'FAILED'
        add_failed_variant(variant_hash, "error_calculation_failure", config['base_config']["failed_variants_file"], lock=db_lock)
        if hasattr(app_module, 'cleanup_variant_files'):
            app_module.cleanup_variant_files(variant_hash, cleanup_conf)
        prune_branch(node)
        return node

    node.error = error

    prof5_file_pattern = os.path.join(config['base_config']["outputs_dir"], f"*{variant_hash}*.prof5")
    possible_files = glob.glob(prof5_file_pattern)
    
    current_energy = float('inf')
    if possible_files:
        prof5_file = possible_files[0]
        try:
            with open(prof5_file, 'r') as f:
                current_energy = float(f.read().strip())
        except Exception as e:
            logging.error(f"Falha ao ler energia para {node.name} no arquivo {prof5_file}: {e}")

    node.energy = current_energy

    # Função de Custo: Ponderação normalizada entre Erro e Redução de Energia
    energy_ratio = current_energy / original_energy if original_energy > 0 else 1.0
    
    # Normalizar para escala 0-1
    # energy_savings: 0 = sem economia, 1 = máxima economia
    energy_savings = max(0.0, 1.0 - energy_ratio)
    
    # normalized_error: erro normalizado (0 = sem erro, 1 = 100% erro)
    # Limitamos a 1.0 para manter na mesma escala
    normalized_error = min(error, 1.0)
    
    # Custo normalizado: ambos componentes na escala 0-1
    # - alpha=0: só importa economia de energia
    # - alpha=1: só importa erro
    # - alpha=0.5: erro e energia têm peso igual
    heuristic_cost = (alpha * normalized_error) + ((1 - alpha) * energy_savings)
    node.cost = heuristic_cost

    if hasattr(app_module, 'save_modified_lines_txt'):
        try:
            sig = inspect.signature(app_module.save_modified_lines_txt)
            num_params = len(sig.parameters)
            
            if num_params == 4:
                original_file = config['pruning_config']['source_file']
                app_module.save_modified_lines_txt(variant_filepath, original_file, variant_hash, config['base_config'])
            elif num_params == 3:
                app_module.save_modified_lines_txt(node.modified_lines, variant_hash, config['base_config'])
            else:
                logging.warning(f"Assinatura inesperada com {num_params} parâmetros em save_modified_lines_txt")
        except Exception as e:
            logging.warning(f"Erro ao chamar save_modified_lines_txt: {e}")

    if heuristic_cost > threshold:
        node.status = 'PRUNED'
        prune_branch(node)
        logging.info(f"Nó {node.name} podado. Custo: {heuristic_cost:.4f} (Err: {normalized_error:.4f}, Savings: {energy_savings:.4f}) > Thr: {threshold}")
        if hasattr(app_module, 'cleanup_variant_files'):
            app_module.cleanup_variant_files(variant_hash, cleanup_conf)
    else:
        node.status = 'COMPLETED'
        logging.info(f"Nó {node.name} aceito. Custo: {heuristic_cost:.4f} <= Thr: {threshold}")
        add_executed_variant(variant_hash, config['base_config']["executed_variants_file"], lock=db_lock)

    return node

def run_tree_pruning_mode(app_module, execution_config, status_monitor, args, db_lock):
    logging.info("Inicializando o modo de Poda de Árvore...")
    
    pruning_config = app_module.get_pruning_config(execution_config)
    if not pruning_config["modifiable_lines"]:
        logging.warning("Nenhuma linha modificável encontrada. Abortando.")
        return

    root = build_variant_tree(pruning_config["modifiable_lines"])
    
    logging.info("Executando a simulação completa da versão original (referência e profiling)...")
    original_hash = gerar_hash_codigo_logico(pruning_config['original_lines'], pruning_config['physical_to_logical'])
    reference_output_path, _ = app_module.simulate_variant(pruning_config['source_file'], original_hash, execution_config, status_monitor, only_spike=False)
    
    if not reference_output_path or not os.path.exists(reference_output_path):
        logging.error("Falha ao gerar a saída de referência e profiling da versão original. Abortando.")
        return

    original_prof5_pattern = os.path.join(execution_config["outputs_dir"], f"*{original_hash}*.prof5")
    possible_original_files = glob.glob(original_prof5_pattern)
    
    original_energy = 1.0
    if possible_original_files:
        try:
            with open(possible_original_files[0], 'r') as f:
                original_energy = float(f.read().strip())
        except Exception as e:
             logging.error(f"Erro ao ler energia original: {e}")

    logging.info(f"Energia Original (Referência): {original_energy}")

    root.status = 'COMPLETED'
    root.error = 0.0
    root.variant_hash = original_hash
    root.energy = original_energy
    root.cost = (1 - args.alpha) * 1.0 
    add_executed_variant(original_hash, execution_config["executed_variants_file"], lock=db_lock)
    
    queue = deque(root.children)
    level = 1
    
    while queue:
        level_size = len(queue)
        logging.info(f"--- Processando Nível {level} ({level_size} nós) ---")
        
        nodes_this_level = [queue.popleft() for _ in range(level_size)]
        max_workers = max(1, os.cpu_count() - 1) if args.workers == 0 else args.workers
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            full_config = {'base_config': execution_config, 'pruning_config': pruning_config}
            futures = {
                executor.submit(process_node, node, app_module, full_config, args.threshold, reference_output_path, status_monitor, db_lock, original_energy, args.alpha): node 
                for node in nodes_this_level
            }

            for future in as_completed(futures):
                node_from_future = futures[future] 
                try:
                    processed_node = future.result()
                    if processed_node.status == 'COMPLETED':
                        for child in processed_node.children:
                            if child.status == 'PENDING':
                                queue.append(child)
                except Exception as e:
                    logging.error(f"Erro catastrófico ao processar o nó {node_from_future.name}: {e}", exc_info=True)
                    node_from_future.status = 'FAILED_UNEXPECTEDLY'
                    prune_branch(node_from_future)

        level += 1

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    tree_report_path = os.path.join(execution_config["logs_dir"], f"pruning_tree_{args.app}_{timestamp}.txt")
    save_tree_to_file(root, tree_report_path)
    
    tree_dot_path = os.path.join(execution_config["logs_dir"], f"pruning_tree_{args.app}_{timestamp}.dot")
    save_tree_to_dot(root, tree_dot_path)
    logging.info(f"Execução de poda concluída. Grafo: {tree_dot_path}")

def main():
    os.environ["PATH"] = f"/opt/riscv/bin:{os.environ['PATH']}"

    parser = argparse.ArgumentParser(description='Simulador de variantes aproximadas')
    parser.add_argument('--app', type=str, default='kinematics', help=f'Tipo de aplicação. Opções: {", ".join(AVAILABLE_APPS.keys())}')
    parser.add_argument('--workers', type=int, default=0, help='Número de workers. 0 para usar CPU count - 1')
    parser.add_argument('--threshold', type=float, default=0.05, help='Limiar máximo de custo permitido para evitar a poda.')
    parser.add_argument('--alpha', type=float, default=1.0, help='Peso do Erro na heurística de custo (0.0 a 1.0). Energia será (1 - alpha).')

    # GRUPO MUTUAMENTE EXCLUSIVO GARANTIDO
    execution_mode_group = parser.add_mutually_exclusive_group(required=True)
    execution_mode_group.add_argument('--forcabruta', action='store_true', help='Executa no modo força bruta (padrão anterior).')
    execution_mode_group.add_argument('--arvorePoda', action='store_true', help='Executa no modo árvore de poda com controlo de variantes e regras.')
    
    args = parser.parse_args()
    
    if not check_dependencies():
        sys.stderr.write("Dependências ausentes. Abortando execução.\n")
        return 1
    
    execution_mode = "forcabruta" if args.forcabruta else "arvorepoda"
    execution_config = create_execution_workspace(args.app, execution_mode, BASE_CONFIG)

    setup_logging(os.path.join(execution_config["logs_dir"], "execucao.log"))

    logging.info(f"=== NOVA EXECUÇÃO INICIADA ===")
    logging.info(f"Aplicação: {args.app}")
    logging.info(f"Modo: {execution_mode}")
    
    if args.arvorePoda:
        logging.info(f"Limiar de Custo (Threshold): {args.threshold}")
        logging.info(f"Alpha (Peso do Erro na Heurística): {args.alpha}")

    import importlib
    app_module_name = AVAILABLE_APPS[args.app]
    app_module = importlib.import_module(app_module_name)

    # Atualiza configuração com os valores do app (suporta ambos os padrões: CONFIG ou get_config)
    if hasattr(app_module, 'get_config'):
        # Novo padrão (apps refatorados com classe)
        app_config = app_module.get_config()
        execution_config.update(app_config)
    elif hasattr(app_module, f"{args.app.upper()}_CONFIG"):
        # Padrão antigo (compatibilidade)
        execution_config.update(getattr(app_module, f"{args.app.upper()}_CONFIG"))

    app_module = setup_environment(args.app, execution_config)
    db_lock = threading.Lock()
    status_monitor = VariantStatusMonitor()

    # BLOCO DE FORÇA BRUTA
    if args.forcabruta:
        logging.info("Executando no modo Força Bruta...")
        variants_to_simulate, _ = app_module.find_variants_to_simulate(execution_config)
        
        checkpoint_exists = os.path.exists(execution_config["checkpoint_file"])
        if checkpoint_exists:
            processed_variants_set, processed_count, total_count = load_checkpoint(execution_config)
            resume = input(f"Encontrado checkpoint com {processed_count}/{total_count} variantes processadas. Continuar? (s/n): ")
            if resume.lower() in ('s', 'sim', 'y', 'yes'):
                variants_to_simulate = [(f, h) for f, h in variants_to_simulate if h not in processed_variants_set]
            else:
                processed_variants_set = set()
        else:
            processed_variants_set = set()
        
        status_monitor.start()
        start_time = datetime.now()
        
        if variants_to_simulate:
            successful_variants = 0
            failed_variants = 0
            max_workers = args.workers if args.workers > 0 else max(1, os.cpu_count() - 1)
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for file, variant_hash in variants_to_simulate:
                    # Chama simulação completa sem lógica de poda
                    futures[executor.submit(
                        app_module.simulate_variant, 
                        file, 
                        variant_hash, 
                        execution_config, 
                        status_monitor
                    )] = (file, variant_hash)
                
                for future in as_completed(futures):
                    file, variant_hash = futures[future]
                    try:
                        result, resume_context = future.result() 
                        if result:
                            successful_variants += 1
                            add_executed_variant(variant_hash, execution_config["executed_variants_file"], lock=db_lock)
                            
                            # Procura arquivo de referência (da execução "original")
                            exe_prefix = execution_config.get("exe_prefix", "app_")
                            outputs_dir = execution_config["outputs_dir"]
                            
                            # Verifica se é a variante original
                            is_original = False
                            if hasattr(app_module, 'get_config'):
                                app_config = app_module.get_config()
                                original_file = app_config.get("original_file", "")
                                is_original = (os.path.abspath(file) == os.path.abspath(original_file))
                            else:
                                is_original = (variant_hash == "original")
                            
                            # Se for a versão original, salva como referência
                            if is_original:
                                ref_output = result + ".reference"
                                try:
                                    import shutil
                                    shutil.copy(result, ref_output)
                                    logging.info(f"Arquivo de referência salvo: {ref_output}")
                                except Exception as e:
                                    logging.warning(f"Não conseguiu salvar referência: {e}")
                            else:
                                # Procura arquivo de referência existente
                                ref_pattern = os.path.join(outputs_dir, f"{exe_prefix}*.reference")
                                ref_files = glob.glob(ref_pattern)
                                
                                # Calcula erro para variantes (só se já existir referência)
                                if ref_files:
                                    reference_file = ref_files[0]
                                    variant_output = result
                                    
                                    if hasattr(app_module, 'calculate_custom_error'):
                                        try:
                                            error = app_module.calculate_custom_error(reference_file, variant_output)
                                            if error is not None:
                                                error_file = variant_output + ".error"
                                                with open(error_file, 'w') as f:
                                                    f.write(f"{error}\n")
                                                logging.info(f"Erro calculado para {variant_hash}: {error:.6f}")
                                            else:
                                                logging.warning(f"calculate_custom_error retornou None para {variant_hash}")
                                        except Exception as e:
                                            logging.warning(f"Erro ao calcular métrica: {e}")
                                else:
                                    logging.warning(f"Nenhum arquivo de referência encontrado para calcular erro de {variant_hash}")
                            
                            # Lógica genérica para identificar o arquivo ORIGINAL
                            # Usa reflexão: tenta get_config() primeiro, depois execution_config
                            original_source_file = None
                            
                            # Tenta obter do método get_config() do app (para apps refatorados)
                            if hasattr(app_module, 'get_config'):
                                try:
                                    app_config = app_module.get_config()
                                    # Tenta diferentes chaves usadas nos apps
                                    for key in ['original_file', 'kernel_source_file', 'tritri_source_file', 'fourier_source_file']:
                                        if key in app_config:
                                            original_source_file = app_config[key]
                                            break
                                except Exception:
                                    pass
                            
                            # Fallback para execution_config
                            if not original_source_file:
                                original_source_file = execution_config.get("original_file", file)

                            save_modified_lines_for_bruteforce(file, original_source_file, variant_hash, app_module, execution_config)
                        else:
                            failed_variants += 1
                            add_failed_variant(variant_hash, "execution_failure", execution_config["failed_variants_file"], lock=db_lock)
                            if hasattr(app_module, 'cleanup_variant_files'):
                                app_module.cleanup_variant_files(variant_hash, execution_config)
                    except Exception as e:
                        failed_variants += 1
                        add_failed_variant(variant_hash, f"exception:{str(e)}", execution_config["failed_variants_file"], lock=db_lock)
                        if hasattr(app_module, 'cleanup_variant_files'):
                            app_module.cleanup_variant_files(variant_hash, execution_config)
                    
                    processed_variants_set.add(variant_hash)
                    if len(processed_variants_set) % 5 == 0:
                        save_checkpoint(len(processed_variants_set), len(variants_to_simulate), 
                                       processed_variants_set, execution_config)
            
            end_time = datetime.now()
            execution_duration = (end_time - start_time).total_seconds()
            report_data = {
                "execution_start": start_time.isoformat(),
                "execution_end": end_time.isoformat(),
                "total_duration_seconds": execution_duration,
                "successful_variants": successful_variants,
                "failed_variants": failed_variants,
                "workers_used": max_workers,
                "app_name": args.app,
                "execution_mode": execution_mode,
                "workspace": execution_config["workspace_path"]
            }
            generate_report(report_data, execution_config)
            
            # Gera relatório de métricas
            generate_metrics_report(args, execution_config, execution_mode, successful_variants, failed_variants)
            
            status_monitor.stop()
            return 0 if failed_variants == 0 else 1
        else:
            status_monitor.stop()
            return 0

    elif args.arvorePoda:
        if not hasattr(app_module, 'get_pruning_config'):
             logging.error(f"Erro: A aplicação '{args.app}' não suporta o modo de poda de árvore.")
             return 1
        
        status_monitor.start()
        run_tree_pruning_mode(app_module, execution_config, status_monitor, args, db_lock)
        
        # Gera relatório de métricas para árvore de poda
        generate_metrics_report(args, execution_config, "arvorePoda", 0, 0)
        
        status_monitor.stop()
        return 0
    
    return 0


def generate_metrics_report(args, execution_config, execution_mode, successful_variants, failed_variants):
    """Gera o relatório de métricas após a execução."""
    try:
        from src.utils.metrics_collector import MetricsCollector
        
        app_name = args.app
        workspace = execution_config.get("workspace_path", "storage")
        
        # Coleta métricas
        collector = MetricsCollector(app_name, workspace)
        collector.collect_all_variants()
        
        # Parâmetros da execução
        execution_params = {
            "mode": execution_mode,
            "workers": args.workers,
            "successful_variants": successful_variants,
            "failed_variants": failed_variants
        }
        
        if hasattr(args, 'alpha'):
            execution_params["alpha"] = args.alpha
        if hasattr(args, 'threshold'):
            execution_params["threshold"] = args.threshold
        
        # Gera/acumula relatório
        report_file = os.path.join(execution_config["logs_dir"], f"metrics_report_{app_name}.json")
        output_file = collector.save_accumulated_report(report_file, execution_params)
        
        logging.info(f"Relatório de métricas salvo em: {output_file}")
        
        # Imprime resumo
        collector.print_summary()
        
    except Exception as e:
        logging.error(f"Erro ao gerar relatório de métricas: {e}")

if __name__ == '__main__':
    sys.exit(main())