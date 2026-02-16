import re

def apply_transformation(line, operation_map):
    """
    Aplica transformações em uma linha de código substituindo operadores aritméticos
    por chamadas de função equivalentes (ex: a * b -> FMULX(a, b)).
    """
    # Ordena operadores pelo tamanho (decrescente) para evitar conflitos (ex: evitar que match em '+' quebre '++')
    # No caso do FFT mapeamos *, +, -
    sorted_ops = sorted(operation_map.keys(), key=len, reverse=True)
    ops_pattern = "|".join([re.escape(op) for op in sorted_ops])
    
    # --- REGEX ROBUSTO PARA OPERANDOS C++ ---
    # Captura identificadores que podem conter:
    # - Letras/Números/Underscore (\w)
    # - Pontos (.) para acesso a membros de struct (ex: t.real)
    # - Colchetes ([, ]) para acesso a arrays (ex: x[i])
    # - Setas (->) para ponteiros
    # - Parênteses ((, )) para chamadas de função simples ou casts
    operand_pattern = r"[\w\.\[\]\->\(\)]+"
    
    # Regex completa: (Operando1) (Espaços) (Operador) (Espaços) (Operando2)
    # Nota: Assume que o código está formatado com espaços ou que os operandos não colam nos operadores
    # de forma ambígua (ex: 'a*b' pode falhar se não houver espaço, mas 'a * b' funciona).
    # O código FFT fornecido usa espaços (ex: 't.real + real_term'), então funcionará bem.
    pattern = re.compile(f"({operand_pattern})\\s*({ops_pattern})\\s*({operand_pattern})")

    def sub_func(match):
        op1 = match.group(1)
        operator = match.group(2)
        op2 = match.group(3)
        
        func_name = operation_map.get(operator, operator)
        return f"{func_name}({op1}, {op2})"

    # Aplica a transformação iterativamente para tratar múltiplos operadores na mesma linha
    # Ex: a + b + c  -->  ADD(a, b) + c  -->  ADD(ADD(a,b), c)
    current_line = line
    max_iters = 10  # Limite de segurança contra loops infinitos
    
    for _ in range(max_iters):
        # Substitui apenas a primeira ocorrência encontrada nesta passagem
        new_line = pattern.sub(sub_func, current_line, count=1)
        if new_line == current_line:
            break
        current_line = new_line
        
    return current_line