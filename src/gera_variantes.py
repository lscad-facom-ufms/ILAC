import os
import argparse
import sys
from config import CONFIG, update_config
from code_parser import parse_code
from generator import generate_variants

def force_print(msg):
    """Imprime mensagem forçando o flush do buffer."""
    print(msg, flush=True)

def main(config_override=None):
    script_dir = os.path.dirname(os.path.abspath(__file__))  
    project_root = os.path.dirname(script_dir) 
    
    base_storage = os.path.join(project_root, "storage")
    
    parser = argparse.ArgumentParser(description='Gerador de variantes de código')
    parser.add_argument('--input', type=str, help='Arquivo de entrada')
    parser.add_argument('--output', type=str, help='Pasta de saída para as variantes')
    parser.add_argument('--executados', type=str, help='Arquivo de variantes executadas')
    # --- NOVOS ARGUMENTOS NECESSÁRIOS ---
    parser.add_argument('--strategy', type=str, help='Estratégia: all, one_hot, etc.')
    parser.add_argument('--max_variantes', type=int, help='Limite de variantes')
    
    if config_override is None:
        args = parser.parse_args()
    else:
        args = argparse.Namespace(input=None, output=None, executados=None, strategy=None, max_variantes=None)

    new_config = {}
    if config_override:
        new_config.update(config_override)

    # Processamento dos argumentos
    if args.input: new_config["input_file"] = os.path.abspath(args.input)
    elif "input_file" not in new_config:
        orig = CONFIG.get("input_file", "")
        if orig and not os.path.isabs(orig):
            new_config["input_file"] = os.path.abspath(os.path.join(project_root, orig))
        else:
            new_config["input_file"] = orig

    if args.output:
        output_folder = os.path.abspath(args.output)
        new_config["output_folder"] = output_folder
        workspace_dir = os.path.dirname(output_folder)
        debug_file = os.path.join(workspace_dir, "variantes_debug.txt")
        linhas_dir = os.path.join(workspace_dir, "linhas_modificadas")
        if "executed_variants_file" not in new_config:
            new_config["executed_variants_file"] = os.path.join(workspace_dir, "executed_variants.json")
    else:
        output_folder = os.path.join(base_storage, "variantes")
        new_config["output_folder"] = output_folder
        debug_file = os.path.join(base_storage, "variantes_debug.txt")
        linhas_dir = os.path.join(base_storage, "linhas_modificadas")

    new_config["debug_file"] = debug_file
    
    if args.executados: new_config["executed_variants_file"] = os.path.abspath(args.executados)
    
    # --- IMPORTANTE: Passar estratégia para a config ---
    if args.strategy: new_config["strategy"] = args.strategy
    if args.max_variantes: new_config["max_variantes"] = args.max_variantes

    update_config(new_config)
    
    # Recupera variáveis
    input_file = CONFIG.get("input_file")
    output_folder = CONFIG.get("output_folder")
    executed_file = CONFIG.get("executed_variants_file")
    operation_map = CONFIG.get("operations_map", {})
    strategy = CONFIG.get("strategy", "one_hot") # Default antigo
    limit = CONFIG.get("max_variantes", None)

    force_print(f"--- Gerador Iniciado ---")
    force_print(f"Input: {input_file}")
    force_print(f"Strategy: {strategy} | Limit: {limit}")

    if not input_file: return []

    try:
        os.makedirs(output_folder, exist_ok=True)
        os.makedirs(linhas_dir, exist_ok=True)
    except Exception as e:
        force_print(f"Erro ao criar diretórios: {e}")
        return []

    try:
        lines, modifiable_lines, physical_to_logical = parse_code(input_file)
        force_print(f"Linhas modificáveis encontradas: {len(modifiable_lines)}")
    except Exception as e:
        force_print(f"Erro no parser: {e}")
        return []
    
    try:
        # Chama a função de geração passando os novos parâmetros
        variants = generate_variants(
            lines, modifiable_lines, physical_to_logical, 
            operation_map, output_folder, os.path.basename(input_file), 
            executed_file,
            limit=limit,
            strategy=strategy
        )
    except Exception as e:
        force_print(f"Erro na geração: {e}")
        import traceback
        traceback.print_exc()
        return []
    
    # Geração de metadados (linhas modificadas)
    if variants:
        force_print("Gerando metadados...")
        try:
            with open(debug_file, 'w') as f_debug:
                f_debug.write(f"Total: {len(variants)}\n\n")
                for variant_file, variant_hash in variants:
                    # Gera arquivo .txt individual para cada variante
                    with open(variant_file, 'r') as vf: variant_lines = vf.readlines()
                    
                    modified_physical = [idx for idx in modifiable_lines 
                                       if idx < len(lines) and lines[idx] != variant_lines[idx]]
                    
                    logical_modified = sorted([physical_to_logical[idx] for idx in modified_physical if idx in physical_to_logical])
                    
                    ind_file = os.path.join(linhas_dir, f"linhas_{variant_hash}.txt")
                    with open(ind_file, 'w') as f_ind:
                        for l in logical_modified: f_ind.write(f"{l}\n")
                        
                    f_debug.write(f"Hash: {variant_hash}, Lines: {logical_modified}\n")

        except Exception as e:
            force_print(f"Erro nos metadados: {e}")

    return variants

if __name__ == "__main__":
    main()