import re
import json
from collections import Counter
import time
import sys
import os

def contar_instrucoes_log(arquivo_log):
    """
    Função Híbrida:
    1. Tenta ler como JSON (formato pré-processado/contado).
    2. Se falhar ou não for JSON, lê como Log Bruto do Spike (contando linha a linha).
    
    Nome mantido como 'contar_instrucoes_log' para compatibilidade com jmeint.py.
    """
    if not os.path.exists(arquivo_log):
        print(f"Erro: Arquivo '{arquivo_log}' não encontrado.")
        return {}

    print(f"Processando arquivo: {arquivo_log}...")
    
    # --- TENTATIVA 1: Ler como JSON (Log pré-processado) ---
    try:
        with open(arquivo_log, 'r', encoding='utf-8') as f:
            # Lê apenas o início para verificar se parece JSON
            inicio = f.read(1024).strip()
            f.seek(0) # Volta para o início
            
            if inicio.startswith('{') or '"' in inicio:
                conteudo = f.read()
                # Tenta load direto
                try:
                    dados = json.loads(conteudo)
                    if isinstance(dados, dict):
                        print("Formato detectado: JSON/Dicionário.")
                        # Filtra e retorna apenas o que é contagem
                        return {k: v for k, v in dados.items() if isinstance(v, (int, float))}
                except json.JSONDecodeError:
                    # Tenta Regex para JSON "sujo" (caso do copy-paste com tags [source..])
                    padrao = r'"([a-zA-Z0-9_\.]+)"\s*:\s*(\d+)'
                    matches = re.findall(padrao, conteudo)
                    if matches:
                        print("Formato detectado: Texto com estrutura JSON.")
                        return {k: int(v) for k, v in matches}
    except Exception:
        pass # Falhou leitura como JSON, segue para Raw Log

    # --- TENTATIVA 2: Ler como Raw Spike Log (Log Bruto) ---
    # Este é o método necessário quando o Spike acaba de rodar
    print("Formato detectado: Log Bruto Spike (iniciando contagem...)")
    
    # Regex para capturar instruções do Spike (core 0: 0x... (0x...) mnemonic)
    instrucao_regex = re.compile(
        r'core\s+\d+:\s+0x[0-9a-f]+\s+\(0x[0-9a-f]+\)\s+([^\s]+)', 
        re.IGNORECASE
    )
    
    contador = Counter()
    linhas_processadas = 0
    inicio_time = time.time()
    
    try:
        with open(arquivo_log, 'r', buffering=8192*16, encoding='utf-8', errors='ignore') as f:
            for linha in f:
                match = instrucao_regex.search(linha)
                if match:
                    instrucao = match.group(1).lower()
                    contador[instrucao] += 1
                linhas_processadas += 1
    except Exception as e:
        print(f"Erro ao ler log bruto: {e}")
        return {}

    tempo_total = time.time() - inicio_time
    print(f"Processamento concluído: {linhas_processadas:,} linhas em {tempo_total:.1f}s")
    
    return dict(contador)

def avaliar_modelo_energia(instrucoes_dict, modelo_path):
    """
    Avalia o modelo de energia com base no dicionário de instruções.
    """
    if not instrucoes_dict:
        return None

    if not os.path.exists(modelo_path):
        print(f"Erro: Modelo de energia '{modelo_path}' não encontrado.")
        return None
    
    try:
        with open(modelo_path, 'r') as f:
            modelo_json = json.load(f)
    except Exception as e:
        print(f"Erro ao ler modelo JSON: {e}")
        return None
    
    core_name = modelo_json.get("core", "unknown")
    freq_mhz = modelo_json.get("freq", 125)
    freq_hz = freq_mhz * 1e6
    
    total_cycles = 0
    total_power_accumulated = 0
    total_instrucoes_mapeadas = 0
    total_instrucoes_log = sum(instrucoes_dict.values())
    
    instrucoes_nao_encontradas = []
    detalhes = {}

    for instrucao, count in instrucoes_dict.items():
        inst_key = instrucao.lower()
        
        if inst_key in modelo_json["insns"]:
            dados_inst = modelo_json["insns"][inst_key]
            i_cycles = dados_inst["cycles"]
            i_power = dados_inst["power"]
            
            inst_total_cycles = i_cycles * count
            inst_total_power_val = i_power * count * i_cycles
            
            total_cycles += inst_total_cycles
            total_power_accumulated += inst_total_power_val
            total_instrucoes_mapeadas += count
            
            detalhes[instrucao] = {
                "count": count,
                "cycles_per_inst": i_cycles,
                "power_val": i_power,
                "total_cycles": inst_total_cycles,
                "total_energy_contribution": inst_total_power_val
            }
        else:
            instrucoes_nao_encontradas.append(instrucao)

    if total_cycles == 0:
        return None

    latency_sec = total_cycles * (1 / freq_hz)
    latency_ms = latency_sec * 1000
    energy_total = (total_power_accumulated * (1 / freq_hz)) * 1000 
    avg_power = energy_total / latency_sec if latency_sec > 0 else 0
    ipc = total_instrucoes_mapeadas / total_cycles

    # Retorna dicionário com chaves compatíveis com o esperado pelo jmeint.py
    resultados = {
        "summary": {
            "core": core_name,
            "freq_MHz": freq_mhz,
            "Total_Inst_Log": total_instrucoes_log,
            "Total_Inst_Mapped": total_instrucoes_mapeadas,
            "Unmapped_Inst_Count": len(instrucoes_nao_encontradas),
            "cycles": total_cycles,
            "IPC": ipc,
            "latency_ms": latency_ms,
            "energy_uW_s": energy_total, 
            "energy_total": energy_total,
            "avg_power": avg_power,
            "power": total_power_accumulated,
            "avg_power_cycle": avg_power
        },
        "detailed": detalhes,
        "unmapped": instrucoes_nao_encontradas
    }
    
    return resultados

# Função Main para teste via linha de comando
def main():
    if len(sys.argv) < 2:
        print("Uso: python3 prof5fake.py <arquivo.log> [--modelo <arquivo.json>]")
        sys.exit(1)
    
    arquivo_log = sys.argv[1]
    modelo_path = None
    
    if "--modelo" in sys.argv:
        try:
            idx = sys.argv.index("--modelo")
            modelo_path = sys.argv[idx + 1]
        except IndexError:
            pass

    instrucoes = contar_instrucoes_log(arquivo_log)
    
    if modelo_path and instrucoes:
        res = avaliar_modelo_energia(instrucoes, modelo_path)
        if res:
            print(json.dumps(res["summary"], indent=2))
            
            # Salvar resultados
            nome_saida = arquivo_log.replace('.log', '_resultados.json')
            if nome_saida == arquivo_log: nome_saida += "_resultados.json"
            try:
                with open(nome_saida, 'w') as f:
                    json.dump(res, f, indent=2)
            except:
                pass

if __name__ == "__main__":
    main()