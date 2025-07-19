import os
import subprocess
import logging
from utils.file_utils import short_hash

def compile_variant(variant_file, variant_hash, config, status_monitor):
    """
    Compila uma variante de kinematics.cpp junto com inversek2j.cpp para gerar o executável.
    """
    is_original = (variant_file == config["original_file"])
    variant_id = "original" if is_original else short_hash(variant_hash)

    status_monitor.update_status(variant_id, "Compilando")

    exe_prefix = config.get("exe_prefix", "app_")
    exe_file = os.path.join(config["executables_dir"], f"{exe_prefix}{variant_hash}")

    # Decide os arquivos a compilar/linkar
    if "compile_files" in config and callable(config["compile_files"]):
        compile_files = config["compile_files"](variant_file, config)
    else:
        compile_files = [variant_file]

    compile_cmd = [
        "riscv32-unknown-elf-g++",
        "-march=rv32imafdc",
        "-I", config["input_dir"],
        "-I", os.path.dirname(config["original_file"]),
        *compile_files,
        "-o", exe_file,
        "-lm"
    ]

    try:
        result = subprocess.run(compile_cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if result.stderr:
            logging.info(f"[Variante {variant_id}] Avisos de compilação: {result.stderr.decode()}")
        logging.info(f"[Variante {variant_id}] Compilação concluída com sucesso")
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro na compilação: {e.stderr.decode()}")
        status_monitor.update_status(variant_id, "Erro na compilação")
        return False

    os.chmod(exe_file, 0o755)
    return True

def generate_dump(exe_file, dump_file, variant_id, status_monitor):
    """Gera o dump do código objeto compilado"""
    status_monitor.update_status(variant_id, "Gerando dump")
    
    dump_cmd = [
        "riscv32-unknown-elf-objdump",
        "-d",
        exe_file
    ]
    
    try:
        with open(dump_file, "w") as df:
            subprocess.run(dump_cmd, check=True, stdout=df, stderr=subprocess.PIPE)
        os.chmod(dump_file, 0o666)
        logging.info(f"[Variante {variant_id}] Dump gerado com sucesso: {dump_file}")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"[Variante {variant_id}] Erro ao gerar dump: {e.stderr.decode()}")
        status_monitor.update_status(variant_id, "Erro no dump")
        return False