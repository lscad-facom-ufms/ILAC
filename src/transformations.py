import re

def apply_transformation(line_content, operations_map):
    """
    Versão aprimorada: Substitui operadores por macros (ex: a + b -> FADDX(a, b))
    e limpa parênteses órfãos para evitar erros de sintaxe.
    """
    # 1. Preparação: Ordena operadores para evitar que '+' combine com '++'
    sorted_ops = sorted(operations_map.keys(), key=len, reverse=True)
    ops_pattern = "|".join([re.escape(op) for op in sorted_ops])
    
    # 2. Regex de operandos: Captura variáveis, membros de struct (.), arrays ([]) e ponteiros (->)
    # Note que removemos o '(' e ')' daqui para que a regex não os considere parte do nome do operando
    operand_pattern = r"[\w\.\[\]\->]+"
    
    # Regex completa: (Operando1) (Espaços) (Operador) (Espaços) (Operando2)
    pattern = re.compile(rf"({operand_pattern})\s*({ops_pattern})\s*({operand_pattern})")

    def replace_with_macro(match):
        arg1 = match.group(1).strip()
        operator = match.group(2)
        arg2 = match.group(3).strip()
        
        # Limpeza Crítica: Remove parênteses que o parser capturou indevidamente
        # Isso evita o erro "expected primary-expression before ','"
        arg1 = arg1.lstrip('(').rstrip(')')
        arg2 = arg2.lstrip('(').rstrip(')')
        
        macro = operations_map.get(operator, operator)
        return f"{macro}({arg1}, {arg2})"

    # 3. Aplicação iterativa para tratar linhas complexas como "a + b + c"
    current_line = line_content
    for _ in range(10):  # Limite de segurança
        new_line = pattern.sub(replace_with_macro, current_line, count=1)
        if new_line == current_line:
            break
        current_line = new_line
    
    return current_line