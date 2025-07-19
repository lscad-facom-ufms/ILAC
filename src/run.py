#!/usr/bin/env python3

import os
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import deque
import logging
import threading # Importar a biblioteca de threading

# Importações gerais
from config_base import BASE_CONFIG
from database.variant_tracker import add_executed_variant, add_failed_variant
from utils.logger import setup_logging, VariantStatusMonitor
from utils.file_utils import ensure_dirs, short_hash, generate_report, save_checkpoint, load_checkpoint
from code_parser import parse_code
from hash_utils import gerar_hash_codigo_logico

# Importações para o modo de poda de árvore
from utils.pruning_tree import build_variant_tree, prune_branch, save_tree_to_file, save_tree_to_dot
from utils.error_analyzer import calculate_error

def save_modified_lines_for_bruteforce(variant_file, original_file, variant_hash, app_module, config):
    """Compara uma variante com o original e salva os índices das linhas modificadas."""
    if not hasattr(app_module, 'save_modified_lines_txt'):
        return

    try:
        # Para apps como kmeans, a função espera os arquivos, hash e config
        # Para outros apps, espera os índices, hash e config
        import inspect
        sig = inspect.signature(app_module.save_modified_lines_txt)
        params = list(sig.parameters)
        # Kmeans: (variant_file, original_file, variant_hash, config)
        if len(params) == 4:
            app_module.save_modified_lines_txt(variant_file, original_file, variant_hash, config)
        else:
            # Gera os índices modificados para apps antigos
            with open(variant_file, 'r') as f_variant, open(original_file, 'r') as f_original:
                variant_lines = f_variant.readlines()
                original_lines = f_original.readlines()
            max_len = max(len(variant_lines), len(original_lines))
            variant_lines.extend([''] * (max_len - len(variant_lines)))
            original_lines.extend([''] * (max_len - len(original_lines)))
            modified_indices = [i for i, (line1, line2) in enumerate(zip(original_lines, variant_lines)) if line1 != line2]
            app_module.save_modified_lines_txt(modified_indices, variant_hash, config)
    except FileNotFoundError:
        logging.warning(f"Arquivo original '{original_file}' ou da variante '{variant_file}' não encontrado para salvar linhas modificadas.")
    except Exception as e:
        logging.error(f"Falha ao salvar índices de linhas modificadas para hash {variant_hash}: {e}")


# Dicionário de aplicações disponíveis
AVAILABLE_APPS = {
    "kinematics": "apps.kinematics",
    "fft": "apps.fft",
    "jmeint": "apps.jmeint",
    "kmeans": "apps.kmeans",
    "sobel": "apps.sobel"  # <-- Adicione esta linha
}

def create_execution_workspace(app_name, execution_mode, base_config):
    """
    Cria um workspace específico para a execução baseado na aplicação e modo.
    
    Args:
        app_name: Nome da aplicação (ex: 'jmeint', 'kinematics')
        execution_mode: Modo de execução ('forcabruta' ou 'arvorepoda')
        base_config: Configuração base do sistema
        
    Returns:
        dict: Nova configuração com caminhos atualizados
    """
    # Timestamp para tornar a pasta única
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Nome da pasta principal: aplicacao_modo_timestamp
    workspace_name = f"{app_name}_{execution_mode}_{timestamp}"
    workspace_path = os.path.join("storage", "executions", workspace_name)
    
    # Criar nova configuração com caminhos atualizados
    execution_config = base_config.copy()
    
    # Atualizar todos os diretórios para ficarem dentro do workspace
    execution_config.update({
        "workspace_path": workspace_path,
        "executables_dir": os.path.join(workspace_path, "executables"),
        "outputs_dir": os.path.join(workspace_path, "outputs"),
        "input_dir": os.path.join(workspace_path, "variants"),
        "logs_dir": os.path.join(workspace_path, "logs"),
        "prof5_results_dir": os.path.join(workspace_path, "prof5_results"),
        "dump_dir": os.path.join(workspace_path, "dumps"),
        "linhas_modificadas_dir": os.path.join(workspace_path, "linhas_modificadas"),  # NOVA PASTA
        "executed_variants_file": os.path.join(workspace_path, "executed_variants.json"),
        "failed_variants_file": os.path.join(workspace_path, "failed_variants.json"),
        "checkpoint_file": os.path.join(workspace_path, "checkpoint.json")
    })
    
    # Criar todos os diretórios
    ensure_dirs(
        execution_config["executables_dir"],
        execution_config["outputs_dir"], 
        execution_config["input_dir"],
        execution_config["logs_dir"],
        execution_config["prof5_results_dir"],
        execution_config["dump_dir"],
        execution_config["linhas_modificadas_dir"]  # CRIAR A NOVA PASTA
    )
    
    # Criar arquivo de informações da execução
    execution_info = {
        "app_name": app_name,
        "execution_mode": execution_mode,
        "timestamp": timestamp,
        "start_time": datetime.now().isoformat(),
        "workspace_path": workspace_path,
        "base_storage": base_config.get("storage_root", "storage")
    }
    
    info_file = os.path.join(workspace_path, "execution_info.json")
    import json
    with open(info_file, 'w') as f:
        json.dump(execution_info, f, indent=2)
    
    logging.info(f"Workspace criado: {workspace_path}")
    logging.info(f"Estrutura de diretórios preparada para {app_name} em modo {execution_mode}")
    
    return execution_config

def check_dependencies():
    """Verifica se todas as ferramentas necessárias estão disponíveis"""
    import shutil
    
    tools = ["riscv32-unknown-elf-g++", "riscv32-unknown-elf-objdump", "spike"]
    missing = []
    
    for tool in tools:
        if not shutil.which(tool):
            missing.append(tool)
    
    if missing:
        logging.error(f"Ferramentas necessárias não encontradas: {', '.join(missing)}")
        return False
    return True

def setup_environment(app_name, execution_config):
    """Prepara o ambiente para execução"""
    # Adiciona o diretório bin do RISC-V ao PATH
    os.environ["PATH"] += ":/opt/riscv/bin"
    
    # Verifica se a aplicação existe
    if app_name not in AVAILABLE_APPS:
        logging.error(f"Erro: Aplicação '{app_name}' não encontrada.")
        return False
    
    # Importa dinamicamente o módulo da aplicação
    try:
        app_module = __import__(AVAILABLE_APPS[app_name], fromlist=[''])
    except ImportError as e:
        logging.error(f"Erro: Não foi possível importar o módulo '{AVAILABLE_APPS[app_name]}': {e}")
        return False
    
    # Gera as variantes de código específicas da aplicação, SEMPRE
    logging.info("Gerando todas as variantes para esta execução...")
    app_module.generate_variants(execution_config)
    
    # Prepara ambiente específico da aplicação
    if not app_module.prepare_environment(execution_config):
        logging.error(f"Erro: Falha ao preparar ambiente para '{app_name}'")
        return False
    
    return app_module

def process_node(node, app_module, config, threshold, reference_output_path, status_monitor, db_lock):
    """Processa um único nó na árvore de variantes: gera, simula, analisa e poda se necessário."""
    if node.status != 'PENDING':
        return node

    node.status = 'SIMULATING'
    
    # 1. Gera a variante específica para este nó
    variant_filepath, variant_hash = app_module.generate_specific_variant(
        config['pruning_config']['original_lines'],
        config['pruning_config']['physical_to_logical'],
        node.modified_lines,
        config['pruning_config']['app_specific_config']
    )
    node.variant_hash = variant_hash

    # 2. Executa a simulação com Spike (Etapa 1)
    variant_output_path, resume_context = app_module.simulate_variant(variant_filepath, variant_hash, config['base_config'], status_monitor, only_spike=True)

    if variant_output_path is None:
        node.status = 'FAILED'
        add_failed_variant(variant_hash, "spike_simulation_failure", config['base_config']["failed_variants_file"], lock=db_lock)
        if hasattr(app_module, 'cleanup_variant_files'):
            cleanup_config = {**config['base_config'], **config['pruning_config']['app_specific_config']}
            app_module.cleanup_variant_files(variant_hash, cleanup_config)
        prune_branch(node)
        return node

    # 3. Analisa a acurácia para determinar o erro
    accuracy = calculate_error(reference_output_path, variant_output_path)

    # Se o cálculo da acurácia falhar, o nó falha
    if accuracy is None:
        node.status = 'FAILED'
        add_failed_variant(variant_hash, "accuracy_calculation_failure", config['base_config']["failed_variants_file"], lock=db_lock)
        if hasattr(app_module, 'cleanup_variant_files'):
            cleanup_config = {**config['base_config'], **config['pruning_config']['app_specific_config']}
            app_module.cleanup_variant_files(variant_hash, cleanup_config)
        prune_branch(node)
        return node

    error = 1.0 - accuracy
    node.error = error

    # SALVAR AS LINHAS MODIFICADAS (ÍNDICES) PARA TODAS AS VARIANTES
    if hasattr(app_module, 'save_modified_lines_txt'):
        app_module.save_modified_lines_txt(node.modified_lines, variant_hash, config['base_config'])

    # 4. Poda o ramo se o erro for muito alto
    if error > threshold:
        node.status = 'PRUNED'
        prune_branch(node)
        logging.info(f"Nó {node.name} podado devido a erro alto ({error:.4f} > {threshold})")
        if hasattr(app_module, 'cleanup_variant_files'):
            cleanup_config = {**config['base_config'], **config['pruning_config']['app_specific_config']}
            app_module.cleanup_variant_files(variant_hash, cleanup_config)
    else:
        # 5. Se o erro for aceitável, continua a execução com a etapa de profiling (Etapa 2)
        logging.info(f"Nó {node.name} passou na verificação de erro. Executando profiling.")
        success = app_module.run_profiling_stage(resume_context, config['base_config'], status_monitor)
        
        if success:
            node.status = 'COMPLETED'
            add_executed_variant(variant_hash, config['base_config']["executed_variants_file"], lock=db_lock)
        else:
            node.status = 'FAILED'
            add_failed_variant(variant_hash, "profiling_stage_failure", config['base_config']["failed_variants_file"], lock=db_lock)
            prune_branch(node)

    return node

def run_tree_pruning_mode(app_module, execution_config, status_monitor, args, db_lock):
    """Lógica principal para o modo de execução com poda de árvore."""
    logging.info("Inicializando o modo de Poda de Árvore...")
    
    pruning_config = app_module.get_pruning_config(execution_config)
    if not pruning_config["modifiable_lines"]:
        logging.warning("Nenhuma linha modificável encontrada. Abortando.")
        return

    root = build_variant_tree(pruning_config["modifiable_lines"])
    logging.info(f"Árvore de variantes construída com {len(root.descendants) + 1} nós potenciais.")

    # Executa a simulação completa da versão original para obter a saída de referência e os dados de profiling
    logging.info("Executando a simulação completa da versão original (referência e profiling)...")
    original_hash = gerar_hash_codigo_logico(pruning_config['original_lines'], pruning_config['physical_to_logical'])
    # A chamada com only_spike=False agora retorna (caminho_saida, None) em sucesso
    reference_output_path, _ = app_module.simulate_variant(pruning_config['source_file'], original_hash, execution_config, status_monitor, only_spike=False)
    
    if not reference_output_path or not os.path.exists(reference_output_path):
        logging.error("Falha ao gerar a saída de referência e profiling da versão original. Abortando.")
        return

    # A simulação completa já foi executada, então o nó raiz está completo
    root.status = 'COMPLETED'
    root.error = 0.0
    root.variant_hash = original_hash
    add_executed_variant(original_hash, execution_config["executed_variants_file"], lock=db_lock)
    
    # Fila para execução nível a nível (Busca em Largura)
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
                executor.submit(process_node, node, app_module, full_config, args.threshold, reference_output_path, status_monitor, db_lock): node 
                for node in nodes_this_level
            }

            for future in as_completed(futures):
                node_from_future = futures[future] # Pega o nó original para referência em caso de erro
                try:
                    processed_node = future.result()
                    logging.info(f"Nó {processed_node.name} finalizado com status: {processed_node.status}")
                    
                    if processed_node.status == 'COMPLETED':
                        for child in processed_node.children:
                            if child.status == 'PENDING':
                                queue.append(child)
                except Exception as e:
                    logging.error(f"Erro catastrófico ao processar o nó {node_from_future.name}: {e}", exc_info=True)
                    # Opcional: Marcar o nó como falho na própria estrutura da árvore
                    node_from_future.status = 'FAILED_UNEXPECTEDLY'
                    prune_branch(node_from_future)

        level += 1

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Salva o relatório em texto
    tree_report_path = os.path.join(execution_config["logs_dir"], f"pruning_tree_{args.app}_{timestamp}.txt")
    save_tree_to_file(root, tree_report_path)
    logging.info(f"Execução com poda de árvore finalizada. Relatório da árvore salvo em: {tree_report_path}")

    # Salva o grafo em .dot
    tree_dot_path = os.path.join(execution_config["logs_dir"], f"pruning_tree_{args.app}_{timestamp}.dot")
    save_tree_to_dot(root, tree_dot_path)
    logging.info(f"Grafo da árvore salvo em: {tree_dot_path}. Use Graphviz para visualizar (dot -Tpng {tree_dot_path} -o tree.png)")

def main():
    """Função principal do programa"""
    # Adiciona o diretório bin do RISC-V ao PATH para encontrar as ferramentas
    os.environ["PATH"] = f"/opt/riscv/bin:{os.environ['PATH']}"

    # Configuração da linha de comando
    parser = argparse.ArgumentParser(description='Simulador de variantes aproximadas')
    parser.add_argument('--app', type=str, default='kinematics',
                      help=f'Tipo de aplicação. Opções: {", ".join(AVAILABLE_APPS.keys())}')
    parser.add_argument('--workers', type=int, default=0,
                      help='Número de workers. 0 para usar CPU count - 1')
    
    # Adiciona grupo para modos de execução mutuamente exclusivos
    execution_mode_group = parser.add_mutually_exclusive_group(required=True)
    execution_mode_group.add_argument('--forcabruta', action='store_true',
                                      help='Executa no modo força bruta (padrão anterior).')
    execution_mode_group.add_argument('--arvorePoda', action='store_true',
                                      help='Executa no modo árvore de poda com controle de variantes e regras.')
    parser.add_argument('--threshold', type=float, default=0.05, help='Limiar de erro para poda da árvore (ex: 0.05 para 5%% de erro).')
    
    args = parser.parse_args()
    
    # Verifica se as dependências estão disponíveis
    if not check_dependencies():
        # O logging ainda não está configurado, então um print é aceitável aqui ou um log para o stderr
        sys.stderr.write("Dependências ausentes. Abortando execução.\n")
        return 1
    
    # Determina o modo de execução para criar o workspace
    execution_mode = "forcabruta" if args.forcabruta else "arvorepoda"
    
    # Cria workspace específico para esta execução
    execution_config = create_execution_workspace(args.app, execution_mode, BASE_CONFIG)

    # DEBUG: Verificar se os caminhos estão corretos
    logging.info(f"DEBUG: input_dir = {execution_config['input_dir']}")
    logging.info(f"DEBUG: executed_variants_file = {execution_config['executed_variants_file']}")

    # Configura o sistema de logging (agora no workspace específico)
    setup_logging(os.path.join(execution_config["logs_dir"], "execucao.log"))

    # Log das informações da execução
    logging.info(f"=== NOVA EXECUÇÃO INICIADA ===")
    logging.info(f"Aplicação: {args.app}")
    logging.info(f"Modo: {execution_mode}")
    logging.info(f"Workspace: {execution_config['workspace_path']}")
    logging.info(f"Timestamp: {datetime.now().isoformat()}")
    logging.info(f"Workers: {args.workers if args.workers > 0 else 'auto'}")
    if args.arvorePoda:
        logging.info(f"Threshold de erro: {args.threshold}")

    # Importa o módulo da aplicação para acessar o config específico
    import importlib

    app_module_name = AVAILABLE_APPS[args.app]
    app_module = importlib.import_module(app_module_name)

    # Atualiza o execution_config com as configs específicas da aplicação
    if hasattr(app_module, f"{args.app.upper()}_CONFIG"):
        execution_config.update(getattr(app_module, f"{args.app.upper()}_CONFIG"))

    # Agora sim, prepara o ambiente
    app_module = setup_environment(args.app, execution_config)
    
    # Lock para proteger o acesso aos arquivos de banco de dados (JSON)
    db_lock = threading.Lock()
    
    # Configura monitor de status de variantes
    status_monitor = VariantStatusMonitor()

    if args.forcabruta:
        logging.info("Executando no modo Força Bruta...")
        logging.info(f"Usando arquivos de controle:")
        logging.info(f"  - executed_variants: {execution_config['executed_variants_file']}")
        logging.info(f"  - failed_variants: {execution_config['failed_variants_file']}")
        logging.info(f"  - checkpoint: {execution_config['checkpoint_file']}")
        
        variants_to_simulate, _ = app_module.find_variants_to_simulate(execution_config)
        logging.info(f"TOTAL DE VARIANTES ENCONTRADAS: {len(variants_to_simulate)}")
        
        # Adicione estes logs para debug
        if len(variants_to_simulate) < 10:  # Se encontrou poucas variantes
            logging.warning(f"DEBUG: Poucas variantes encontradas. Listando todas:")
            for i, (file, hash_val) in enumerate(variants_to_simulate):
                logging.info(f"DEBUG: Variante {i+1}: {os.path.basename(file)} - {hash_val[:8]}")
            
            # Verificar se há variantes no diretório
            variant_dir = execution_config["input_dir"]
            all_files = os.listdir(variant_dir) if os.path.exists(variant_dir) else []
            logging.info(f"DEBUG: Arquivos no diretório de variantes: {len(all_files)}")
            logging.info(f"DEBUG: Primeiros arquivos: {all_files[:10]}")
        
        # Carrega checkpoint apenas se existir no workspace atual
        checkpoint_exists = os.path.exists(execution_config["checkpoint_file"])
        if checkpoint_exists:
            processed_variants_set, processed_count, total_count = load_checkpoint(execution_config)
            logging.info(f"CHECKPOINT ENCONTRADO NO WORKSPACE ATUAL: processed={processed_count}, total={total_count}")
            resume = input(f"Encontrado checkpoint com {processed_count}/{total_count} variantes processadas. Continuar? (s/n): ")
            if resume.lower() in ('s', 'sim', 'y', 'yes'):
                variants_to_simulate = [(f, h) for f, h in variants_to_simulate if h not in processed_variants_set]
                logging.info(f"Continuando execução com {len(variants_to_simulate)} variantes pendentes...")
            else:
                processed_variants_set = set()
                logging.info("Reiniciando execução do zero...")
        else:
            logging.info("Nova execução - nenhum checkpoint encontrado no workspace atual")
            processed_variants_set = set()
        
        status_monitor.start()
        start_time = datetime.now()
        
        if variants_to_simulate:
            logging.info(f"Processando {len(variants_to_simulate)} variantes...")
            successful_variants = 0
            failed_variants = 0
            
            if args.workers > 0:
                max_workers = args.workers
            else:
                max_workers = max(1, os.cpu_count() - 1)
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {}
                for file, variant_hash in variants_to_simulate:
                    futures[executor.submit(
                        app_module.simulate_variant, 
                        file, 
                        variant_hash, 
                        execution_config,  # Usar execution_config em vez de BASE_CONFIG
                        status_monitor
                    )] = (file, variant_hash)
                
                for future in as_completed(futures):
                    file, variant_hash = futures[future]
                    try:
                        result, _ = future.result() # Desempacota a tupla (caminho, contexto)
                        if result:
                            successful_variants += 1
                            logging.info(f"Simulação da variante {short_hash(variant_hash)} concluída com sucesso")
                            add_executed_variant(variant_hash, execution_config["executed_variants_file"], lock=db_lock)
                            
                            # Para KMEANS:
                            if hasattr(app_module, "KMEANS_CONFIG"):
                                original_source_file = app_module.KMEANS_CONFIG["original_file"]
                            elif hasattr(app_module, "JMEINT_CONFIG"):
                                original_source_file = app_module.JMEINT_CONFIG["tritri_source_file"]
                            else:
                                # fallback genérico
                                original_source_file = execution_config.get("original_file", file)

                            save_modified_lines_for_bruteforce(file, original_source_file, variant_hash, app_module, execution_config)
                        else:
                            failed_variants += 1
                            logging.warning(f"Falha na simulação da variante {short_hash(variant_hash)}")
                            add_failed_variant(variant_hash, "execution_failure", execution_config["failed_variants_file"], lock=db_lock)
                            if hasattr(app_module, 'cleanup_variant_files'):
                                app_module.cleanup_variant_files(variant_hash, execution_config)
                    except Exception as e:
                        failed_variants += 1
                        logging.error(f"Erro ao processar a variante {short_hash(variant_hash)}: {e}")
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
            logging.info(f"Processamento concluído: {successful_variants} com sucesso, {failed_variants} falhas")
            logging.info(f"Resultados salvos em: {execution_config['workspace_path']}")
            status_monitor.stop()
            return 0 if failed_variants == 0 else 1
        else:
            logging.info("Nenhuma variante nova para simular")
            status_monitor.stop()
            return 0

    elif args.arvorePoda:
        if not hasattr(app_module, 'get_pruning_config'):
             logging.error(f"Erro: A aplicação '{args.app}' não suporta o modo de poda de árvore.")
             return 1
        
        status_monitor.start()
        run_tree_pruning_mode(app_module, execution_config, status_monitor, args, db_lock)
        logging.info(f"Resultados da poda de árvore salvos em: {execution_config['workspace_path']}")
        status_monitor.stop()
        return 0
    
    logging.info("Processamento concluído!")
    return 0

if __name__ == '__main__':
    sys.exit(main())