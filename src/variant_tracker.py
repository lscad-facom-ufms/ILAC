import os

def load_executed_variants(file_path="executados.txt"):
    """Carrega os hashes das variantes já executadas"""
    executed = set()
    if os.path.exists(file_path):
        with open(file_path, "r") as f:
            for line in f:
                executed.add(line.strip())
    return executed

def add_executed_variant(codigo_hash, file_path="executados.txt"):
    """Adiciona o hash de uma variante executada no arquivo"""
    with open(file_path, "a") as f:
        f.write(codigo_hash + "\n")

def is_variant_executed(codigo_hash, file_path="executados.txt"):
    """Verifica se uma variante já foi executada"""
    executed = load_executed_variants(file_path)
    return codigo_hash in executed