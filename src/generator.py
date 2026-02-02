import os
import argparse
from itertools import combinations
from transformations import apply_transformation
from database.variant_tracker import load_executed_variants
from hash_utils import gerar_hash_codigo_logico

# Adicionados argumentos limit e strategy na definição da função
def generate_variants(lines, modifiable_lines, physical_to_logical, operation_map, output_folder, file_name, executed_file="executados.txt", limit=None, strategy="all"):
    """
    Gera variantes do código substituindo operações nas linhas modificáveis.
    
    Args:
        strategy: "all" (combinatorial - todas as combinações), "one_hot" (apenas 1 modificação por vez).
        limit: número máximo de variantes a gerar (segurança).
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"Pasta de saída criada: {output_folder}")

    # Carrega as variantes já executadas para evitar duplicatas
    executed_variants = load_executed_variants(executed_file)
    
    modified_files = []
    skipped = 0
    generated_count = 0
    
    # Lógica de Estratégia
    if strategy == "one_hot":
        # Apenas combinações de 1 elemento
        range_comb = range(1, 2)
    else:
        # Força Bruta / All: Combinações de 1 até N elementos (gera 1023 para 10 linhas)
        range_comb = range(1, len(modifiable_lines) + 1)

    print(f"Iniciando geração. Estratégia: {strategy}, Modifiable Lines: {len(modifiable_lines)}")

    # Loop principal de geração
    for r in range_comb:
        # Se atingiu o limite, para o loop externo
        if limit and generated_count >= limit:
            break

        for combination in combinations(modifiable_lines, r):
            # Checagem de Limite Global
            if limit and generated_count >= limit:
                print(f"Limite de variantes atingido ({limit}). Parando geração.")
                return modified_files

            modified_lines = lines.copy()  # Cópia fresca das linhas originais
            
            # Aplicar substituições apenas nas linhas selecionadas nesta combinação
            for idx in combination:
                modified_lines[idx] = apply_transformation(modified_lines[idx], operation_map)
            
            # Gerar hash lógico
            codigo_hash = gerar_hash_codigo_logico(modified_lines, physical_to_logical)
            
            # Verifica se a variante já foi executada
            if codigo_hash in executed_variants:
                skipped += 1
                if skipped % 500 == 0:
                    print(f"Variante já executada (skip): {codigo_hash[:8]}")
                continue
                
            # Nome do arquivo de saída com Hash
            nome_base, extensao = os.path.splitext(file_name)
            output_file = f"{nome_base}_{codigo_hash}{extensao}"
            output_path = os.path.join(output_folder, output_file)
            
            # Salvamento do arquivo
            with open(output_path, 'w', newline='') as f:
                f.writelines(modified_lines)
            
            modified_files.append((output_path, codigo_hash))
            generated_count += 1
            
            if generated_count % 500 == 0:
                print(f"Geradas {generated_count} variantes até agora...")
    
    print(f"Geração finalizada.")
    print(f"Total de variantes novas geradas: {len(modified_files)}")
    print(f"Total de variantes puladas (já existentes): {skipped}")
    
    return modified_files