import logging
import sys
import os
import time
import threading

# Lock global para sincronização de logs entre threads
LOG_LOCK = threading.Lock()

def setup_logging(log_file="execucoes.log", console_level=logging.INFO, file_level=logging.INFO):
    """Configura o sistema de logging para o arquivo e console"""
    # Configura o logger raiz
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Nível mais baixo para capturar tudo
    
    # Remove handlers existentes para evitar duplicação
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Handler para o console
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)
    
    # Handler para o arquivo
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(file_level)
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(processName)s - %(threadName)s - %(message)s", 
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    file_handler.setFormatter(file_formatter)
    root_logger.addHandler(file_handler)
    
    return root_logger

# Classe para monitorar o status das variantes
class VariantStatusMonitor:
    def __init__(self):
        self.status_dict = {}
        self.status_lock = threading.Lock()
        self.last_status = {}
        self.stop_event = threading.Event()
        self.monitor_thread = None
    
    def update_status(self, variant, message):
        """Atualiza o status de uma variante e retorna True se houve mudança"""
        with self.status_lock:
            if variant not in self.status_dict or self.status_dict[variant] != message:
                self.status_dict[variant] = message
                return True
            return False
    
    def _monitor_loop(self):
        """Loop de monitoramento que roda em uma thread separada"""
        while not self.stop_event.is_set():
            with self.status_lock:
                # Verifica se houve alguma mudança desde o último status
                changes = []
                for variant, status in self.status_dict.items():
                    if variant not in self.last_status or self.last_status[variant] != status:
                        changes.append((variant, status))
                        self.last_status[variant] = status
                
                # Só imprime se houver mudanças
                if changes:
                    logging.info("------ Atualizações de Status ------")
                    for variant, status in sorted(changes):  # Ordena por variante
                        logging.info(f"Variante {variant}: {status}")
                    logging.info("------ Fim das atualizações ------\n")
            
            time.sleep(0.5)  # Reduz o intervalo de verificação para ser mais responsivo
    
    def start(self):
        """Inicia o monitoramento em uma thread separada"""
        if self.monitor_thread is None or not self.monitor_thread.is_alive():
            self.stop_event.clear()
            self.monitor_thread = threading.Thread(target=self._monitor_loop)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
            logging.info("Monitoramento de status iniciado")
    
    def stop(self):
        """Para o monitoramento"""
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.stop_event.set()
            self.monitor_thread.join(timeout=2)  # Espera no máximo 2 segundos
            logging.info("Monitoramento de status finalizado")