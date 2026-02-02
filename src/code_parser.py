import re
import logging

def parse_code(file_path):
    """
    Analisa um arquivo de código-fonte para identificar linhas modificáveis.
    Espera comentários como //anotacao: na linha anterior à operação alvo.
    
    Retorna:
        lines: conteúdo do arquivo
        modifiable_lines: lista de índices (int) das linhas que podem ser alteradas
        physical_to_logical: mapa de índice físico -> lógico
    """
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        logging.error(f"Arquivo não encontrado: {file_path}")
        return [], [], {}
    
    modifiable_lines = []
    physical_to_logical = {}
    logical_line_count = 0
    
    # Padrão para encontrar anotações: //anotacao: ou /*anotacao:*/
    # Aceita opcionalmente '\' no final para quebra de linha em macros
    annotation_pattern = re.compile(r'^\s*(?://anotacao:|/\*anotacao:\*/)\s*(?:\\)?\s*$')
    
    for i, line in enumerate(lines):
        # Ignora linhas totalmente em branco para contagem lógica
        if re.match(r'^\s*$', line):
            continue
        
        # Verifica se a linha atual é uma anotação
        if annotation_pattern.match(line):
            # A linha seguinte (i + 1) é a que será modificada
            if i + 1 < len(lines):
                modifiable_lines.append(i + 1)
            continue 
        
        # Contagem de linhas lógicas (para métricas e hashes consistentes)
        logical_line_count += 1
        physical_to_logical[i] = logical_line_count
    
    # AVISO CRÍTICO: Se não achou nada, o usuário precisa saber
    if not modifiable_lines:
        msg = (
            f"[AVISO PARSER] Nenhuma linha modificável encontrada em: {file_path}\n"
            f"DICA: O parser busca por linhas contendo estritamente '//anotacao:' "
            f"imediatamente antes da linha de código alvo."
        )
        print(msg) # Força print no stdout
        logging.warning(msg)
    
    return lines, modifiable_lines, physical_to_logical