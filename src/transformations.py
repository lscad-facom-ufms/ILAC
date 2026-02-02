import re

def apply_transformation(line, operation_map):
    """
    Aplica transformações em uma linha de código substituindo operadores aritméticos
    por chamadas de função equivalentes (ex: a * b -> FMULX(a, b)).
    """
    # Regex para capturar operandos (variáveis, constantes, chamadas de função)
    operand = (
        r'(?:'
        # Acesso via ponteiro: p->g
        r'[a-zA-Z_]\w*->\w+'
        # Chamada de função ou array complexo: func(args) ou arr[i](args)
        r'|(?:[a-zA-Z_]\w*(?:\[[^\]]+\])*)\([^\)]*\)' 
        # Acesso a array (suporte a multidimensional [i][j]):
        r'|[a-zA-Z_]\w*(?:\[[^\]]+\])+' 
        # Identificador simples: var, count
        r'|[a-zA-Z_]\w*'
        # Literal numérico (float/int): 123, -4.5, 1.0f
        r'|-?[0-9\.]+(?:f|F)?'
        # Literal numérico entre parênteses: (-1)
        r'|\(-?[0-9\.]+\)'
        r')'
    )
    
    # Itera sobre cada operador mapeado (ex: *, +, -)
    for op, func in operation_map.items():
        if op in line:
            escaped_op = re.escape(op)
            
            # Regex principal: procura Operando op Operando
            # Evita substituir se já for uma chamada da função alvo (FMULX(...))
            pattern = re.compile(
                r'(?P<before>(?:^|[\(=,\s]))' # Captura o que vem antes (início, parenteses, atribuição)
                r'(?P<expr>(?!' + re.escape(func) + r'\()' # Negative lookahead: não pode ser FMULX(
                r'(?:' + operand + r'(?:\s*' + escaped_op + r'\s*' + operand + r')+))' # Op1 * Op2
                r'(?P<after>(?=[,\)\;])|\s*$)' # O que vem depois (fim, vírgula, ponto-vírgula)
            )

            def sub_func(m):
                before = m.group("before")
                after = m.group("after")
                expr = m.group("expr")
                
                # Extrai todos os operandos da expressão encontrada
                operand_pattern = re.compile(operand)
                operands = operand_pattern.findall(expr)
                
                if not operands:
                    return m.group(0) # Segurança: se falhar, não altera

                # Reconstrói como chamada de função aninhada: FMULX(a, b)
                result = operands[0]
                for opnd in operands[1:]:
                    result = f"{func}({result}, {opnd})"
                
                return f"{before}{result}{after}"

            # Loop para garantir que todas as ocorrências na linha sejam tratadas
            # Adicionado limite max_iter para evitar loops infinitos em casos patológicos
            max_iter = 20
            count = 0
            new_line = line
            while count < max_iter:
                processed_line = pattern.sub(sub_func, new_line)
                if processed_line == new_line:
                    break
                new_line = processed_line
                count += 1
            
            line = new_line
    
    return line