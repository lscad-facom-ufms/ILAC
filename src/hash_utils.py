import hashlib
import re

def gerar_hash_codigo(codigo_fonte):
    """Normaliza o código (removendo espaços finais e uniformizando quebras de linha)
    e gera um hash SHA256 do código-fonte."""
    # Divide em linhas, remove espaços finais, e força quebra de linha '\n'
    linhas_normalizadas = [linha.rstrip() for linha in codigo_fonte.splitlines()]
    codigo_normalizado = "\n".join(linhas_normalizadas)
    return hashlib.sha256(codigo_normalizado.encode()).hexdigest()


def gerar_hash_codigo_logico(lines, physical_to_logical):
    """
    Gera o hash SHA256 baseado somente nas linhas lógicas.
    Remove espaços a esquerda e direita, substituindo múltiplos espaços por um único espaço.
    """
    logical_lines = []
    for i in sorted(physical_to_logical.keys()):
        # Remove espaços extras e normaliza
        lin = lines[i].strip()
        lin = re.sub(r'\s+', ' ', lin)
        logical_lines.append(lin)
    codigo_logico = "\n".join(logical_lines)
    return hashlib.sha256(codigo_logico.encode()).hexdigest()