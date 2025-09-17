import re

def parse_code(file_path):
    """
    Analisa um arquivo de código-fonte para identificar linhas modificáveis.
    Retorna as linhas do código, as linhas modificáveis e o mapeamento físico para lógico.
    """
    with open(file_path, 'r') as f:
        lines = f.readlines()
    
    modifiable_lines = []
    physical_to_logical = {}
    logical_line_count = 0
    
    # Padrão para encontrar qualquer tipo de anotação, opcionalmente seguida por '\'
    # Isso cobre:
    # //anotacao:
    # /*anotacao:*/
    # //anotacao: \
    # /*anotacao:*/ \
    annotation_pattern = re.compile(r'^\s*(?://anotacao:|/\*anotacao:\*/)\s*(?:\\)?\s*$')
    
    for i, line in enumerate(lines):
        # Ignora linhas em branco
        if re.match(r'^\s*$', line):
            continue
        
        # Verifica se a linha contém a anotação
        if annotation_pattern.match(line):
            # A linha seguinte à anotação é considerada modificável
            if i + 1 < len(lines):
                modifiable_lines.append(i + 1) # Adiciona o índice físico (base 0) da linha de código
            continue  # Pula a contagem lógica para as linhas de anotação
        
        logical_line_count += 1
        physical_to_logical[i] = logical_line_count
    
    return lines, modifiable_lines, physical_to_logical

def encontrar_blocos_approx(codigo):
    """
    Encontra blocos de código marcados com #pragma approx.
    Retorna lista de tuplas (inicio, fim, bloco).
    """
    # Regex para encontrar blocos entre #pragma approx e #pragma endapprox
    padrao = re.compile(
        r'#pragma\s+approx\s*\n(.*?)#pragma\s+endapprox',
        re.DOTALL | re.IGNORECASE
    )
    blocos = []
    for match in padrao.finditer(codigo):
        inicio = match.start(1)
        fim = match.end(1)
        bloco = match.group(1)
        blocos.append((inicio, fim, bloco))
    return blocos

# Exemplo de uso:
# with open('meuarquivo.cpp') as f:
#     codigo = f.read()
# blocos = encontrar_blocos_approx(codigo)
# for inicio, fim, bloco in blocos:
#     print("Bloco approx encontrado:", bloco)