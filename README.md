# PaCA: Projeto de Análise de Computação Aproximada

Framework completo para geração, simulação, análise e rastreamento de variantes de código aproximado, com suporte a aplicações científicas, integração com toolchain RISC-V, profiler Prof5, análise de erros e execução paralela.

---

## Sumário

- [Visão Geral](#visão-geral)
- [Funcionalidades](#funcionalidades)
- [Fluxo Completo do Projeto](#fluxo-completo-do-projeto)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Instalação e Configuração do Ambiente](#instalação-e-configuração-do-ambiente)
- [Como Usar](#como-usar)
  - [Execução de Aplicações](#execução-de-aplicações)
  - [Geração de Variantes](#geração-de-variantes)
  - [Adição de Novas Aplicações](#adição-de-novas-aplicações)
  - [Análise de Resultados](#análise-de-resultados)
  - [Rastreamento de Variantes](#rastreamento-de-variantes)
- [Arquivos de Controle](#arquivos-de-controle)
- [Licença](#licença)

---

## Visão Geral

O PaCA automatiza a geração de variantes aproximadas de código, compila, simula, analisa desempenho e precisão, e rastreia variantes em experimentos de computação aproximada, especialmente para arquitetura RISC-V.

---

## Funcionalidades

- Geração automática de variantes de código com operadores aproximados.
- Compilação cruzada para RISC-V.
- Simulação automatizada via Spike.
- Análise de desempenho com Prof5.
- Análise de erro entre variantes e referência.
- Rastreamento de variantes executadas, falhas e checkpoints.
- Poda de variantes redundantes.
- Suporte a múltiplas aplicações científicas.
- Execução paralela (multi-thread).
- Logs detalhados e organização de resultados.

---

## Fluxo Completo do Projeto

1. **Preparação do ambiente**: Instalação do ambiente Docker já configurado.
2. **Geração de variantes**: Criação automática de múltiplas versões do código base com operadores aproximados.
3. **Compilação**: Cada variante é compilada para a arquitetura RISC-V.
4. **Simulação**: As variantes são executadas no simulador Spike.
5. **Análise de desempenho**: O Prof5 coleta métricas detalhadas de execução.
6. **Análise de erro**: As saídas das variantes são comparadas com a referência para medir precisão.
7. **Rastreamento e controle**: O sistema registra variantes executadas, falhas e checkpoints para retomada.
8. **Poda de variantes**: Variantes redundantes são eliminadas para otimizar o processo.
9. **Armazenamento e logs**: Todos os resultados, logs e artefatos são organizados em diretórios específicos.

---

## Estrutura do Projeto

```
PaCA/
├── src/
│   ├── apps/           # Aplicações suportadas (fft, kmeans, etc)
│   ├── database/       # Rastreamento de variantes
│   ├── execution/      # Compilação e simulação
│   ├── utils/          # Utilitários (análise de erro, poda, logging)
│   ├── code_parser.py
│   ├── config.py
│   ├── generator.py
│   ├── gera_variantes.py
│   ├── run.py
│   └── transformations.py
├── data/
│   └── reference/      # Funções aproximadas (approx.h)
├── storage/
│   ├── dump/           # Dumps de código objeto
│   ├── executable/     # Executáveis compilados
│   ├── logs/           # Logs de execução
│   ├── output/         # Saídas das simulações
│   └── prof5_results/  # Resultados do profiler
└── codigos_modificados/ # Variantes de código geradas
```

---

## Instalação e Configuração do Ambiente

**Recomendado: use o container Docker já pronto!**

1. **Baixe e execute o container Docker:**
   ```sh
   docker pull gregoriokn/lscad_approx:v2
   docker run -it --rm -v $(pwd):/workspace -w /workspace gregoriokn/lscad_approx:v2 /bin/bash
   ```
   > O container já possui o toolchain RISC-V, Spike e dependências Python instaladas.

2. **(Opcional) Instale dependências Python adicionais:**
   ```sh
   pip install -r requirements.txt
   ```

3. **Configure caminhos** em `src/config_base.py` e `src/config.py` conforme seu ambiente/diretórios.

---

## Como Usar

### Execução de Aplicações

Execute uma aplicação suportada (ex: fft, kmeans):

```sh
python src/run.py --app [nome_da_aplicacao] --workers [num_threads]
```
- `--app`: Nome da aplicação (ex: fft, kmeans)
- `--workers`: Número de threads para paralelização (opcional)

### Geração de Variantes

Gere variantes de código a partir de um arquivo fonte:

```sh
python src/gera_variantes.py --input [arquivo_entrada] --output [pasta_saida]
```
- `--input`: Caminho do arquivo fonte base
- `--output`: Pasta onde as variantes serão salvas

### Adição de Novas Aplicações

1. Crie um novo módulo em `src/apps/` (ex: `minha_app.py`)
2. Implemente as funções:
   - `prepare_environment(base_config)`
   - `generate_variants(base_config)`
   - `find_variants_to_simulate(base_config)`
   - `simulate_variant(variant_file, variant_hash, base_config, status_monitor)`
3. Adicione ao dicionário `AVAILABLE_APPS` em `src/run.py`

### Análise de Resultados

- Resultados das simulações: `storage/output/`
- Resultados do profiler: `storage/prof5_results/`
- Logs de execução: `storage/logs/`
- Análise de erro: utilize `src/utils/error_analyzer.py`

### Rastreamento de Variantes

- Controle de variantes executadas: `executados.txt`
- Registro de falhas: `falhas.txt`
- Checkpoint para retomada: `checkpoint.txt`
- Rastreamento detalhado: `src/database/variant_tracker.py`

---

## Arquivos de Controle

- `executados.txt`: Variantes já simuladas
- `falhas.txt`: Variantes que falharam
- `checkpoint.txt`: Estado para retomada automática

---

## Licença

Projeto acadêmico. Todos os direitos reservados.
