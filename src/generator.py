import os
import hashlib
from itertools import combinations
from datetime import datetime
from transformations import apply_transformation
from database.variant_tracker import load_executed_variants
from hash_utils import gerar_hash_codigo_logico

def generate_variants(lines, modifiable_lines, physical_to_logical, operation_map, output_folder, file_name, executed_file="executados.txt"):
    """
    Gera variantes do código substituindo operações nas linhas modificáveis.
    Mantém registro de variantes já geradas utilizando arquivo de texto.
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"Pasta de saída criada: {output_folder}")

    base_path = os.path.join(output_folder, os.path.splitext(file_name)[0])
    
    # Carrega as variantes já executadas
    executed_variants = load_executed_variants(executed_file)
    
    modified_files = []
    skipped = 0
    
    # Para cada combinação possível de linhas a modificar
    for r in range(1, len(modifiable_lines) + 1):
        for combination in combinations(modifiable_lines, r):
            modified_lines = lines.copy()  # Cópia fresca das linhas
            
            # Aplicar substituições apenas nas linhas selecionadas
            for idx in combination:
                modified_lines[idx] = apply_transformation(modified_lines[idx], operation_map)
            
            # Gerar hash para rastreamento
            codigo_hash = gerar_hash_codigo_logico(modified_lines, physical_to_logical)
            
            # Verifica se a variante já foi executada
            if codigo_hash in executed_variants:
                skipped += 1
                if skipped % 10 == 0:  # Limita mensagens de log
                    print(f"Variante já executada, pulando: {codigo_hash[:8]} (total pulado: {skipped})")
                continue
                
            # Nome do arquivo de saída
            nome_base, extensao = os.path.splitext(file_name)
            # Modifique esta linha para usar o hash no nome do arquivo
            output_file = f"{nome_base}_{codigo_hash}{extensao}"
            
            # Salvamento com tratamento especial para manter formatação
            with open(os.path.join(output_folder, output_file), 'w', newline='') as f:
                f.writelines(modified_lines)
            
            print(f"Variante salva: {output_file}")
            modified_files.append((os.path.join(output_folder, output_file), codigo_hash))
    
    print(f"Total de variantes geradas: {len(modified_files)}")
    print(f"Total de variantes puladas: {skipped}")
    
    return modified_files