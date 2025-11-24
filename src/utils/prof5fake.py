import re
import json
from collections import Counter
import time
import sys
import os

def contar_instrucoes_log(arquivo_log):
    """Conta instruções no log e retorna dicionário com frequências"""
    # Regex UNIVERSAL para capturar TODAS as possíveis instruções RISC-V
    # Captura qualquer sequência de caracteres válidos após o padrão de endereço
    instrucao_regex = re.compile(
        r'core\s+\d+:\s+0x[0-9a-f]+\s+\(0x[0-9a-f]+\)\s+([^\s]+)', 
        re.IGNORECASE
    )
    
    contador = Counter()
    linhas_processadas = 0
    
    print(f"Iniciando processamento de {arquivo_log}...")
    inicio = time.time()
    
    # Processar linha por linha (eficiente em memória)
    with open(arquivo_log, 'r', buffering=8192*16) as f:
        for linha in f:
            match = instrucao_regex.search(linha)
            if match:
                instrucao = match.group(1).lower()  # Normaliza para minúsculo
                contador[instrucao] += 1
            
            linhas_processadas += 1
            
            # Progress feedback a cada 1 milhão de linhas
            # if linhas_processadas % 1000000 == 0:
            #     tempo_decorrido = time.time() - inicio
            #     print(f"Processadas {linhas_processadas:,} linhas em {tempo_decorrido:.1f}s")
    
    tempo_total = time.time() - inicio
    print(f"Processamento concluído: {linhas_processadas:,} linhas em {tempo_total:.1f}s")
    print(f"Total de instruções encontradas: {sum(contador.values()):,}")
    
    # Converter Counter para dict e salvar como JSON
    resultado_dict = dict(contador)
    
    # Salvar como JSON com nome baseado no arquivo de entrada
    nome_saida = arquivo_log.replace('.log', '_instrucoes.json')
    with open(nome_saida, 'w') as json_file:
        json.dump(resultado_dict, json_file, indent=2, sort_keys=True)
    
    print(f"Resultado salvo em: {nome_saida}")
    return resultado_dict

def gerar_modelo_template(instrucoes_dict, nome_arquivo="modelo_template.json"):
    """Gera um template de modelo de energia baseado nas instruções encontradas"""
    modelo_template = {
        "core": "core-template",
        "freq": 125,  # Frequência padrão em MHz (igual ao seu exemplo)
        "power": 0,
        "insns": {}
    }
    
    # Popular o dicionário de instruções com valores padrão
    for instrucao in instrucoes_dict.keys():
        modelo_template["insns"][instrucao] = {
            "cycles": 1,    # Valor padrão
            "power": 0.5    # Valor padrão (você pode ajustar)
        }
    
    # Salvar template
    with open(nome_arquivo, 'w') as json_file:
        json.dump(modelo_template, json_file, indent=2, sort_keys=True)
    
    print(f"Template de modelo salvo em: {nome_arquivo}")
    return modelo_template

def avaliar_modelo_energia(instrucoes_dict, modelo_path):
    """Avalia o modelo de energia aplicando-o às instruções contadas"""
    if not os.path.exists(modelo_path):
        print(f"Erro: Arquivo de modelo '{modelo_path}' não encontrado.")
        return None
    
    # Carregar modelo de energia
    with open(modelo_path, 'r') as json_file:
        modelo_json = json.load(json_file)
    
    core_name = modelo_json.get("core", "unknown")
    freq = modelo_json.get("freq", 125) * 1e6  # MHz para Hz
    
    # print(f"Aplicando modelo: {core_name} @ {modelo_json.get('freq', 125)}MHz")
    
    # Calcular métricas
    total_cycles = 0
    total_power = 0
    total_instrucoes = 0  # Apenas instruções mapeadas
    total_instrucoes_log = 0  # TODAS as instruões do log
    instrucoes_nao_encontradas = []

    # print("\nCalculando métricas por instrução:")
    for instrucao, count in instrucoes_dict.items():
        total_instrucoes_log += count  # Sempre conta
        
        if instrucao in modelo_json["insns"]:
            i_cycles = modelo_json["insns"][instrucao]["cycles"]
            i_power = modelo_json["insns"][instrucao]["power"]
            
            inst_total_cycles = i_cycles * count
            inst_total_power = i_power * count * i_cycles
            
            total_cycles += inst_total_cycles
            total_power += inst_total_power
            total_instrucoes += count  # Apenas mapeadas
            
            # print(f"  {instrucao:15} x {count:>8,} = {inst_total_cycles:>10,} cycles, {inst_total_power:>12.2f} power")
        else:
            instrucoes_nao_encontradas.append(instrucao)
            # NÃO adiciona em total_instrucoes

    # PRINT DAS INSTRUÇÕES NÃO MAPEADAS
    if instrucoes_nao_encontradas:
        # print(f"\n{'='*60}")
        # print(f"AVISO: {len(instrucoes_nao_encontradas)} INSTRUÇÕES NÃO ENCONTRADAS NO MODELO")
        # print(f"{'='*60}")
        
        # Ordenar por frequência (mais frequentes primeiro)
        instrucoes_nao_encontradas_ordenadas = sorted(
            [(inst, instrucoes_dict[inst]) for inst in instrucoes_nao_encontradas], 
            key=lambda x: x[1], reverse=True
        )
        
        total_nao_mapeadas = sum(instrucoes_dict[inst] for inst in instrucoes_nao_encontradas)
        # print(f"Total de instruções não mapeadas: {total_nao_mapeadas:,}")
        
        # print("\nTop 30 instruções não mapeadas:")
        # for i, (inst, count) in enumerate(instrucoes_nao_encontradas_ordenadas[:30], 1):
        #     percentage = (count / total_instrucoes_log) * 100
        #     print(f"{i:2d}. {inst:20} {count:>10,} vezes ({percentage:5.2f}%)")
        
        # if len(instrucoes_nao_encontradas) > 30:
        #     restantes = len(instrucoes_nao_encontradas) - 30
        #     print(f"\n... e mais {restantes} instruções não mostradas")
        
        # print(f"\n{'='*60}")

    # Calcular métricas finais
    total_latency = total_cycles * (1 / freq)  # segundos
    total_energy = (total_power * (1 / freq)) * 1000  # µW*s
    ipc = total_instrucoes / total_cycles if total_cycles > 0 else 0
    avg_power_cycle = total_power / total_cycles if total_cycles > 0 else 0
    
    # Para compatibilidade com prof5 original, use apenas instruções mapeadas
    total_instrucoes_contadas = total_instrucoes  # Apenas mapeadas
    
    # Calcular IPC baseado apenas nas instruções que têm modelo
    ipc = total_instrucoes_contadas / total_cycles if total_cycles > 0 else 0
    
    # Resumo de todas as métricas (similar ao profiler original)
    resultados = {
        "summary": {
            "Total_Inst": total_instrucoes_contadas,      # Apenas mapeadas
            "Total_Inst_All_Log": total_instrucoes_log,  # ← Para referência
            "Unmapped_Instructions": total_instrucoes_log - total_instrucoes_contadas,
            "IPC": ipc,
            "Total_Unique_Inst": len(instrucoes_dict),
            "cycles": total_cycles,
            "latency_seconds": total_latency,
            "latency_ms": total_latency * 1000,
            "energy_uW_s": total_energy,
            "power": total_power,
            "avg_power_cycle": avg_power_cycle,
            "freq_MHz": modelo_json.get('freq', 125),
            "core": core_name
        },
        "detailed": {}
    }
    
    # Detalhes por instrução
    for instrucao, count in instrucoes_dict.items():
        if instrucao in modelo_json["insns"]:
            i_cycles = modelo_json["insns"][instrucao]["cycles"]
            i_power = modelo_json["insns"][instrucao]["power"]
            inst_total_cycles = i_cycles * count
            inst_total_power = i_power * count * i_cycles
            
            resultados["detailed"][instrucao] = {
                "count": count,
                "cycles_per_inst": i_cycles,
                "power_per_inst": i_power,
                "total_cycles": inst_total_cycles,
                "total_power": inst_total_power,
                "percentage_of_total": (count / total_instrucoes) * 100
            }
    
    return resultados

def main():
    if len(sys.argv) < 2:
        print("Uso: python3 prof5fake.py <arquivo.log> [--modelo modelo.json] [--template]")
        print("Exemplos:")
        print("  python3 prof5fake.py execution_Exato_blackscholes.log")
        print("  python3 prof5fake.py execution_Exato_blackscholes.log --template")
        print("  python3 prof5fake.py execution_Exato_blackscholes.log --modelo meu_modelo.json")
        sys.exit(1)

    arquivo_entrada = sys.argv[1]
    gerar_template = "--template" in sys.argv
    modelo_path = None

    if "--modelo" in sys.argv:
        try:
            idx = sys.argv.index("--modelo")
            if idx + 1 < len(sys.argv):
                modelo_path = sys.argv[idx + 1]
        except:
            print("Erro: Especifique o caminho do modelo após --modelo")
            sys.exit(1)

    try:
        # Agora lê o .log como JSON direto
        if arquivo_entrada.endswith('.log'):
            with open(arquivo_entrada, 'r') as f:
                resultado = json.load(f)
            print(f"Arquivo .log (JSON) de instruções carregado: {arquivo_entrada}")
        else:
            print("Erro: O arquivo de entrada deve ser um .log no formato JSON.")
            sys.exit(1)

        if not resultado:
            print("Nenhuma instrução encontrada no formato esperado.")
            return

        # Etapa 2: Gerar template se solicitado
        if gerar_template:
            template_name = arquivo_entrada.replace('.log', '_template.json')
            gerar_modelo_template(resultado, template_name)
        
        # Etapa 3: Avaliar modelo se fornecido
        if modelo_path:
            print(f"\n{'='*60}")
            print("AVALIAÇÃO DO MODELO DE ENERGIA")
            print(f"{'='*60}")
            
            resultados_energia = avaliar_modelo_energia(resultado, modelo_path)
            
            if resultados_energia:
                # Salvar resultados
                resultado_nome = arquivo_entrada.replace('.log', '_resultados.json')
                with open(resultado_nome, 'w') as json_file:
                    json.dump(resultados_energia, json_file, indent=2, sort_keys=True)
                
                # Exibir resumo
                summary = resultados_energia["summary"]
                print(f"\n{'='*60}")
                print("RESUMO FINAL")
                print(f"{'='*60}")
                print(f"Core:                     {summary['core']}")
                print(f"Frequência:               {summary['freq_MHz']} MHz")
                print(f"Total de Instruções:      {summary['Total_Inst']:,}")
                print(f"Instruções Únicas:        {summary['Total_Unique_Inst']:,}")
                print(f"IPC:                      {summary['IPC']:.4f}")
                print(f"Total de Ciclos:          {summary['cycles']:,}")
                print(f"Latência:                 {summary['latency_ms']:.2f} ms")
                print(f"Energia Total:            {summary['energy_uW_s']:.2f} µW*s")
                print(f"Potência Total:           {summary['power']:.2f}")
                print(f"Potência Média/Ciclo:     {summary['avg_power_cycle']:.4f}")
                print(f"\nResultados detalhados salvos em: {resultado_nome}")
        
        # Etapa 4: Mostrar top instruções
        print(f"\n{'='*60}")
        print("TOP 20 INSTRUÇÕES MAIS FREQUENTES")
        print(f"{'='*60}")
        sorted_results = sorted(resultado.items(), key=lambda x: x[1], reverse=True)[:20]
        for i, (instrucao, count) in enumerate(sorted_results, 1):
            percentage = (count / sum(resultado.values())) * 100
            print(f"{i:2d}. {instrucao:15} {count:>10,} ({percentage:5.2f}%)")
        
    except FileNotFoundError:
        print(f"Erro: Arquivo '{arquivo_entrada}' não encontrado.")
        sys.exit(1)
    except Exception as e:
        print(f"Erro inesperado: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()