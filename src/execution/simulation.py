import os
import time
import subprocess
import logging
import json
from utils.file_utils import short_hash, TempFiles
from database.variant_tracker import add_executed_variant

def run_spike_simulation(exe_file, input_file, output_file, spike_log_file, variant_id, status_monitor):
    """
    Executa a simulação da variante utilizando o simulador RISC-V Spike.
    Retorna o tempo de execução ou None em caso de erro.
    """
    status_monitor.update_status(variant_id, "Simulando com Spike")
    logging.info(f"[Variante {variant_id}] Iniciando simulação com Spike...")
    
    # Cria o arquivo de saída vazio (necessário para o spike)
    open(output_file, 'w').close()
    os.chmod(output_file, 0o666)
    
    # Comando para execução do Spike
    sim_cmd = [
        "spike",
        "--isa=RV32IMAFDC",
        "-l",
        f"--log={spike_log_file}",
        "/opt/riscv/riscv32-unknown-elf/bin/pk",
        exe_file,
        input_file,
        output_file
    ]
    
    # Executa o spike e mede o tempo
    start = time.perf_counter()
    try:
        result = subprocess.run(
            sim_cmd,
            capture_output=True,
            text=True,
            timeout=600  # ajuste se necessário
        )
        if result.returncode != 0:
            logging.error(f"[Variante {variant_id}] Erro na simulação (Spike):\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
            print(f"[Variante {variant_id}] Erro na simulação (Spike):\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
            return None
        if result.stderr:
            logging.info(f"[Variante {variant_id}] Saída de erro do Spike: {result.stderr.decode()}")
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro na simulação: {e.stderr.decode()}")
        status_monitor.update_status(variant_id, "Erro na simulação")
        return None
    end = time.perf_counter()
    
    runtime = end - start
    logging.info(f"[Variante {variant_id}] Simulação concluída em {runtime:.6f} segundos.")
    
    return runtime

def run_prof5(exe_file, log_file, dump_file, prof5_model, prof5_executable, 
             prof5_time_file, prof5_report_path, variant_id, status_monitor):
    """
    Executa o profiler Prof5 na variante.
    Retorna o tempo de execução ou None em caso de erro.
    """
    status_monitor.update_status(variant_id, "Executando Prof5")
    logging.info(f"[Variante {variant_id}] Iniciando execução do Prof5...")
    
    # Comando para execução do Prof5
    prof5_cmd = [
        prof5_executable,
        "-i", "RV32IMAFDC",
        "-l", log_file,
        "-d", dump_file,
        "-m", prof5_model,
        exe_file
    ]
    
    # Executa o prof5 e mede o tempo
    start = time.perf_counter()
    try:
        subprocess.run(prof5_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro ao executar Prof5: {e.stderr.decode()}")
        status_monitor.update_status(variant_id, "Erro no Prof5")
        return None
    end = time.perf_counter()
    
    runtime = end - start
    logging.info(f"[Variante {variant_id}] Prof5 executado em {runtime:.6f} segundos.")
    
    # Salva o tempo do prof5
    with open(prof5_time_file, "w") as pf:
        pf.write(f"{runtime}\n")
    os.chmod(prof5_time_file, 0o666)
    
    # Verifica se o arquivo de relatório foi gerado
    if os.path.exists(prof5_report_path):
        logging.info(f"[Variante {variant_id}] Relatório do Prof5 gerado: {prof5_report_path}")
    else:
        logging.warning(f"[Variante {variant_id}] Relatório do Prof5 não foi gerado")
    
    return runtime

def simulate_variant(variant_file, variant_hash, config, status_monitor, code_parser):
    """
    Compila e executa a simulação completa para uma variante.
    Atualiza o arquivo de variantes executadas após a conclusão bem-sucedida.
    """
    from execution.compilation import compile_variant, generate_dump
    
    # Se for o arquivo original, usa "original" como identificador nos logs
    is_original = (variant_file == config["original_file"])
    variant_id = "original" if is_original else short_hash(variant_hash)
    
    # Definir nomes de arquivos
    exe_file = os.path.join(config["executables_dir"], f"kinematics_{variant_hash}")
    output_file = os.path.join(config["outputs_dir"], f"kinematics_{variant_hash}.data")
    time_file = os.path.join(config["outputs_dir"], f"kinematics_{variant_hash}.time")
    spike_log_file = os.path.join(config["logs_dir"], f"kinematics_{variant_hash}.log")
    prof5_time_file = os.path.join(config["outputs_dir"], f"kinematics_{variant_hash}.prof5")
    prof5_report_path = os.path.join("prof5Results", f"prof5_results_{variant_hash}.json")
    dump_file = os.path.join("dump", f"dump_{variant_hash}.txt")
    
    # Usa o gerenciador de contexto para arquivos temporários
    with TempFiles([spike_log_file, dump_file]):
        # Passo 1: Compilar a variante
        if not compile_variant(variant_file, variant_hash, config, status_monitor):
            return False
        
        # Passo 2: Gerar o dump
        if not generate_dump(exe_file, dump_file, variant_id, status_monitor):
            return False
        
        # Passo 3: Executar a simulação com Spike
        sim_time = run_spike_simulation(
            exe_file, 
            config["train_data_input"], 
            output_file, 
            spike_log_file, 
            variant_id, 
            status_monitor
        )
        if sim_time is None:
            return False
        
        # Salva o tempo de simulação
        with open(time_file, 'w') as tf:
            tf.write(f"{sim_time}\n")
        os.chmod(time_file, 0o666)
        
        # Passo 4: Executar o Prof5
        prof5_time = run_prof5(
            exe_file, 
            spike_log_file, 
            dump_file, 
            config["prof5_model"],
            config["prof5_executable"],
            prof5_time_file,
            prof5_report_path,
            variant_id,
            status_monitor
        )
        if prof5_time is None:
            return False
        
        # Salvar as linhas modificadas para análise posterior
        save_modified_lines(variant_file, config["original_file"], variant_hash, config, code_parser)
    
    # Marca a variante como executada com sucesso
    add_executed_variant(variant_hash)
    
    logging.info(f"[Variante {variant_id}] Simulação completa finalizada com sucesso!")
    status_monitor.update_status(variant_id, "Concluída")
    
    return True

def save_modified_lines(variant_file, original_file, variant_hash, config, code_parser):
    """Salva as linhas modificadas em um arquivo texto para análise"""
    # Obtém os dados necessários
    lines_output_file = os.path.join(config["outputs_dir"], f"linhas_hash_{variant_hash}.txt")
    
    # Lê o código original e da variante
    with open(original_file, "r") as f:
        original_lines = f.readlines()
    with open(variant_file, "r") as f:
        modified_lines = f.readlines()
    
    # Obtém o mapeamento físico-lógico
    _, __, physical_to_logical = code_parser(original_file)
    
    # Obtém as linhas modificadas
    modified_logical_lines = get_modified_logical_lines(original_lines, modified_lines, physical_to_logical)
    
    # Salva no arquivo
    with open(lines_output_file, "w") as f:
        for line in modified_logical_lines:
            f.write(str(line) + "\n")
    
    logging.info(f"Linhas modificadas salvas para variante {short_hash(variant_hash)}")

def get_modified_logical_lines(original_lines, modified_lines, physical_to_logical):
    """
    Identifica as linhas lógicas modificadas entre os arquivos original e modificado.
    """
    # Primeiro, encontra as linhas marcadas com //anotacao:
    import re
    
    modifiable_lines = []
    for i, line in enumerate(original_lines):
        if re.match(r'^\s*//anotacao:\s*$', line):
            if i + 1 < len(original_lines):
                modifiable_lines.append(i + 1)
    
    # Verifica quais dessas linhas foram realmente modificadas
    modified_logical_lines = []
    for physical_line in modifiable_lines:
        if physical_line < len(original_lines) and physical_line < len(modified_lines):
            orig = re.sub(r'\s+', ' ', original_lines[physical_line].strip())
            mod = re.sub(r'\s+', ' ', modified_lines[physical_line].strip())
            
            if orig != mod and physical_line in physical_to_logical:
                modified_logical_lines.append(physical_to_logical[physical_line])
    
    return sorted(modified_logical_lines)