import os
import argparse
import sys
from config import CONFIG, update_config
from code_parser import parse_code
from generator import generate_variants

def force_print(msg):
    """Imprime mensagem forçando o flush do buffer para aparecer nos logs do subprocesso."""
    print(msg, flush=True)

def main(config_override=None):
    # Obter o diretório raiz do projeto (onde está a pasta src)
    script_dir = os.path.dirname(os.path.abspath(__file__))  
    project_root = os.path.dirname(script_dir) 
    
    # Configuração padrão de caminhos
    base_storage = os.path.join(project_root, "storage")
    
    # Argumentos da linha de comando
    parser = argparse.ArgumentParser(description='Gerador de variantes de código')
    parser.add_argument('--input', type=str, help='Arquivo de entrada')
    parser.add_argument('--output', type=str, help='Pasta de saída para as variantes')
    parser.add_argument('--executados', type=str, help='Arquivo de variantes executadas')
    
    # NOVOS ARGUMENTOS ADICIONADOS PARA CONTROLE DE GERAÇÃO
    parser.add_argument('--strategy', type=str, help='Estratégia de geração (ex: "one_hot", "all", "combinatorial")')
    parser.add_argument('--max_variantes', type=int, help='Limite máximo de variantes a gerar')
    
    # Parseia apenas se não houver override direto (chamada via função)
    if config_override is None:
        args = parser.parse_args()
    else:
        # Simula args vazios se chamado como módulo para evitar erro de atributo
        args = argparse.Namespace(input=None, output=None, executados=None, strategy=None, max_variantes=None)

    # Dicionário para atualizar a configuração
    new_config = {}
    if config_override:
        new_config.update(config_override)

    # 1. Processar Arquivo de Entrada
    if args.input:
        new_config["input_file"] = os.path.abspath(args.input)
    elif "input_file" not in new_config:
        # Fallback para o config original, garantindo caminho absoluto
        orig_input = CONFIG.get("input_file", "")
        if orig_input and not os.path.isabs(orig_input):
            new_config["input_file"] = os.path.abspath(os.path.join(project_root, orig_input))
        else:
            new_config["input_file"] = orig_input

    # 2. Processar Estratégia e Limites (CRÍTICO PARA O FIX DO FFT)
    if args.strategy:
        new_config["strategy"] = args.strategy
    if args.max_variantes:
        new_config["max_variantes"] = args.max_variantes

    # 3. Processar Pasta de Saída (Output)
    if args.output:
        output_folder = os.path.abspath(args.output)
        new_config["output_folder"] = output_folder
        
        # Se output for passado (ex: .../workspace/variants), 
        # definimos o diretório base do workspace como o pai dele.
        workspace_dir = os.path.dirname(output_folder)
        
        # Define caminhos auxiliares DENTRO do workspace
        debug_file = os.path.join(workspace_dir, "variantes_debug.txt")
        linhas_dir = os.path.join(workspace_dir, "linhas_modificadas")
        # Default de arquivo de variáveis executadas quando estamos em modo workspace
        default_executed = os.path.join(workspace_dir, "executed_variants.txt")
        if "executed_variants_file" not in new_config:
            new_config["executed_variants_file"] = default_executed
    else:
        # Comportamento legado (sem workspace)
        output_folder = os.path.join(base_storage, "variantes")
        new_config["output_folder"] = output_folder
        debug_file = os.path.join(base_storage, "variantes_debug.txt")
        linhas_dir = os.path.join(base_storage, "linhas_modificadas")

    new_config["debug_file"] = debug_file

    # 4. Processar Arquivo de Executados
    if args.executados:
        new_config["executed_variants_file"] = os.path.abspath(args.executados)
    elif "executed_variants_file" in CONFIG:
        orig_exec = CONFIG["executed_variants_file"]
        if orig_exec and not os.path.isabs(orig_exec):
             new_config["executed_variants_file"] = os.path.abspath(os.path.join(project_root, orig_exec))

    # Atualiza a configuração global (Isso fará o generator.py ver a nova strategy se ele usar CONFIG)
    update_config(new_config)
    
    # Recupera valores finais
    input_file = CONFIG.get("input_file")
    output_folder = CONFIG.get("output_folder")
    executed_file = CONFIG.get("executed_variants_file")
    operation_map = CONFIG.get("operations_map", {})
    
    # Recupera estratégia para log
    strategy = CONFIG.get("strategy", "default")
    max_vars = CONFIG.get("max_variantes", "unlimited")

    force_print(f"--- Iniciando Geração de Variantes ---")
    force_print(f"Input: {input_file}")
    force_print(f"Output Dir: {output_folder}")
    force_print(f"Strategy: {strategy} | Max Variants: {max_vars}")
    force_print(f"Debug File: {debug_file}")
    
    if not input_file:
        force_print("ERRO: Arquivo de entrada não especificado.")
        return []

    # Criar diretórios necessários
    try:
        os.makedirs(output_folder, exist_ok=True)
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        os.makedirs(linhas_dir, exist_ok=True)
    except Exception as e:
        force_print(f"ERRO CRÍTICO ao criar diretórios: {e}")
        return []

    # Parse do código
    try:
        lines, modifiable_lines, physical_to_logical = parse_code(input_file)
        force_print(f"Código analisado. {len(modifiable_lines)} linhas modificáveis encontradas.")
    except Exception as e:
        force_print(f"ERRO ao analisar código fonte: {e}")
        return []
    
    # Geração das variantes
    try:
        # Nota: Assume-se que generate_variants lê 'strategy' e 'max_variantes' do CONFIG global
        # ou que você atualizará generator.py se ele exigir argumentos explícitos.
        # Como generator.py não foi fornecido, a atualização via CONFIG é a via correta aqui.
        variants = generate_variants(
            lines, modifiable_lines, physical_to_logical, 
            operation_map, output_folder, os.path.basename(input_file), 
            executed_file
        )
    except Exception as e:
        force_print(f"ERRO na função generate_variants: {e}")
        import traceback
        traceback.print_exc()
        return []
    
    num_variants = len(variants) if variants else 0
    force_print(f"Geração finalizada. {num_variants} variantes geradas.")
    
    try:
        with open(os.path.join(output_folder, ".variants_generated"), "w") as _f:
            _f.write("done\n")
    except Exception:
        pass
    
    if not variants:
        force_print("AVISO: Lista de variantes vazia.")
        return []
    
    # Pós-processamento
    force_print("Gerando metadados (arquivos de linhas modificadas)...")
    try:
        with open(debug_file, 'w') as f_debug:
            f_debug.write(f"Ref: {input_file}\n")
            f_debug.write(f"Strategy: {strategy}\n")
            f_debug.write(f"Total: {num_variants}\n\n")

            for i, (variant_file, variant_hash) in enumerate(variants, 1):
                with open(variant_file, 'r') as vf:
                    variant_lines = vf.readlines()
                
                modified_physical_indices = []
                for idx in modifiable_lines:
                    if idx < len(lines) and idx < len(variant_lines):
                        if lines[idx].strip() != variant_lines[idx].strip():
                            modified_physical_indices.append(idx)
                
                logical_modified = sorted([physical_to_logical.get(idx) for idx in modified_physical_indices if idx in physical_to_logical])
                
                individual_file = os.path.join(linhas_dir, f"linhas_{variant_hash}.txt")
                with open(individual_file, 'w') as f_ind:
                    for logical_line in logical_modified:
                        f_ind.write(f"{logical_line}\n")
                
                f_debug.write(f"Variante #{i} [{variant_hash}]\n")
                f_debug.write(f"  File: {os.path.basename(variant_file)}\n")
                f_debug.write(f"  Modificações Lógicas: {logical_modified}\n")
                f_debug.write("-" * 40 + "\n")
                
                if i % 500 == 0:
                    force_print(f"Processados metadados de {i}/{num_variants} variantes...")

        force_print(f"Metadados gerados com sucesso em: {linhas_dir}")
        
    except Exception as e:
        force_print(f"ERRO durante geração de metadados: {e}")
    
    return variants

if __name__ == "__main__":
    main()