import json
import os
import logging
import threading
from datetime import datetime

class VariantCache:
    """Classe singleton para gerenciar o cache de variantes executadas"""
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, file_path="executados.txt"):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(VariantCache, cls).__new__(cls)
                cls._instance.file_path = file_path
                cls._instance.variants = set()
                cls._instance.file_lock = threading.Lock()
                cls._instance._load_cache()
            elif cls._instance.file_path != file_path:
                # Se o caminho mudou, recarrega o cache
                cls._instance.file_path = file_path
                cls._instance.variants.clear()
                cls._instance._load_cache()
        return cls._instance
    
    def _load_cache(self):
        """Carrega os hashes das variantes do arquivo para o cache"""
        if os.path.exists(self.file_path):
            with open(self.file_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.variants.add(line)
            logging.info(f"Cache inicializado com {len(self.variants)} variantes do arquivo {self.file_path}")
        else:
            logging.warning(f"Arquivo {self.file_path} não encontrado durante inicialização do cache!")
    
    def add_variant(self, codigo_hash):
        """Adiciona uma variante ao cache e ao arquivo de forma thread-safe"""
        # Verifica se a variante já está no cache
        if codigo_hash in self.variants:
            return False
        
        # Adiciona ao cache e ao arquivo de forma atômica
        with self.file_lock:
            # Verifica novamente para garantir que outra thread não adicionou 
            # enquanto estávamos esperando o lock
            if codigo_hash in self.variants:
                return False
                
            # Adiciona ao cache
            self.variants.add(codigo_hash)
            
            # Adiciona ao arquivo
            try:
                with open(self.file_path, "a") as f:
                    f.write(codigo_hash + "\n")
                logging.info(f"Variante {codigo_hash[:8]} adicionada ao arquivo {self.file_path}")
                return True
            except Exception as e:
                # Em caso de erro, remove do cache para manter consistência
                self.variants.remove(codigo_hash)
                logging.error(f"Erro ao adicionar variante {codigo_hash[:8]} ao arquivo: {e}")
                return False
    
    def contains(self, codigo_hash):
        """Verifica se uma variante está no cache"""
        return codigo_hash in self.variants
    
    def get_all_variants(self):
        """Retorna uma cópia do conjunto de variantes"""
        return self.variants.copy()


def load_executed_variants(file_path, lock=None):
    """Carrega o conjunto de hashes de variantes já executadas de um arquivo JSON."""
    def do_load():
        if not os.path.exists(file_path):
            return {}
        try:
            with open(file_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    if lock:
        with lock:
            return do_load()
    else:
        return do_load()

def add_executed_variant(variant_hash, file_path, lock=None):
    """Adiciona o hash de uma variante executada com sucesso ao arquivo JSON."""
    def do_add():
        variants = load_executed_variants(file_path) # Não precisa de lock aqui, já estamos dentro de um
        variants[variant_hash] = {"status": "success", "timestamp": datetime.now().isoformat()}
        try:
            with open(file_path, 'w') as f:
                json.dump(variants, f, indent=2)
            os.chmod(file_path, 0o666)
        except IOError as e:
            # Adicionar log de erro se o logging estiver configurado
            print(f"Erro ao escrever no arquivo de variantes executadas: {e}")

    if lock:
        with lock:
            do_add()
    else:
        do_add()


def add_failed_variant(variant_hash, reason, file_path, lock=None):
    """Adiciona o hash de uma variante que falhou ao arquivo JSON."""
    def do_add_failed():
        variants = load_executed_variants(file_path) # Não precisa de lock aqui, já estamos dentro de um
        variants[variant_hash] = {"status": "failed", "reason": reason, "timestamp": datetime.now().isoformat()}
        try:
            with open(file_path, 'w') as f:
                json.dump(variants, f, indent=2)
            os.chmod(file_path, 0o666)
        except IOError as e:
            print(f"Erro ao escrever no arquivo de variantes falhas: {e}")

    if lock:
        with lock:
            do_add_failed()
    else:
        do_add_failed()