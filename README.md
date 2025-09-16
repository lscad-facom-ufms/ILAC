# PaCA: Project for Approximate Computing Analysis

A complete framework for the generation, simulation, analysis, and tracking of approximate code variants, with support for scientific applications, integration with the RISC-V toolchain, Prof5 profiler, error analysis, and parallel execution.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Complete Project Workflow](#complete-project-workflow)
- [Project Structure](#project-structure)
- [Environment Installation and Configuration](#environment-installation-and-configuration)
- [How to Use](#how-to-use)
  - [Running Applications](#running-applications)
  - [Generating Variants](#generating-variants)
  - [Adding New Applications](#adding-new-applications)
  - [Analyzing Results](#analyzing-results)
  - [Tracking Variants](#tracking-variants)
- [Control Files](#control-files)
- [License](#license)

---

## Overview

PaCA automates the generation of approximate code variants, compiles, simulates, analyzes performance and accuracy, and tracks variants in approximate computing experiments, especially for the RISC-V architecture.

---

## Features

- Automatic generation of code variants with approximate operators.
- Cross-compilation for RISC-V.
- Automated simulation via Spike.
- Performance analysis with Prof5.
- Error analysis between variants and the reference.
- Tracking of executed variants, failures, and checkpoints.
- Pruning of redundant variants.
- Support for multiple scientific applications.
- Parallel execution (multi-threaded).
- Detailed logs and organization of results.

---

## Complete Project Workflow

1.  **Environment preparation**: Installation of the pre-configured Docker environment.
2.  **Variant generation**: Automatic creation of multiple versions of the base code with approximate operators.
3.  **Compilation**: Each variant is compiled for the RISC-V architecture.
4.  **Simulation**: The variants are executed in the Spike simulator.
5.  **Performance analysis**: Prof5 collects detailed execution metrics.
6.  **Error analysis**: The outputs of the variants are compared with the reference to measure accuracy.
7.  **Tracking and control**: The system records executed variants, failures, and checkpoints for resumption.
8.  **Variant pruning**: Redundant variants are eliminated to optimize the process.
9.  **Storage and logs**: All results, logs, and artifacts are organized into specific directories.

---

## Project Structure

```
PaCA/
├── src/
│   ├── apps/             # Supported applications (fft, kmeans, etc)
│   ├── database/         # Variant tracking
│   ├── execution/        # Compilation and simulation
│   ├── utils/            # Utilities (error analysis, pruning, logging)
│   ├── code_parser.py
│   ├── config.py
│   ├── generator.py
│   ├── generate_variants.py
│   ├── run.py
│   └── transformations.py
├── data/
│   └── reference/        # Approximate functions (approx.h)
├── storage/
│   ├── dump/             # Object code dumps
│   ├── executable/       # Compiled executables
│   ├── logs/             # Execution logs
│   ├── output/           # Simulation outputs
│   └── prof5_results/    # Profiler results
└── modified_codes/       # Generated code variants
```

---

## Environment Installation and Configuration

**Recommended: use the pre-built Docker container!**

1. **Download and run the Docker container:**
   ```sh
   docker pull gregoriokn/lscad_approx:v2
   docker run -it --rm -v $(pwd):/workspace -w /workspace gregoriokn/lscad_approx:v2 /bin/bash
   ```
   > The container already includes the RISC-V toolchain, Spike, and Python dependencies.

2. **(Optional) Install additional Python dependencies:**
   ```sh
   pip install -r requirements.txt
   ```

3. **Configure paths** in `src/config_base.py` and `src/config.py` according to your environment/directories.

---

## How to Use

### Running Applications

Run a supported application (e.g., fft, kmeans):

```sh
python src/run.py --app [application_name] --workers [num_threads]
```
- `--app`: Application name (e.g., fft, kmeans)
- `--workers`: Number of threads for parallelization (optional)

### Generating Variants

Generate code variants from a source file:

```sh
python src/generate_variants.py --input [input_file] --output [output_folder]
```
- `--input`: Path to the base source file
- `--output`: Folder where the variants will be saved

### Adding New Applications

1. Create a new module in `src/apps/` (e.g., `my_app.py`)
2. Implement the functions:
   - `prepare_environment(base_config)`
   - `generate_variants(base_config)`
   - `find_variants_to_simulate(base_config)`
   - `simulate_variant(variant_file, variant_hash, base_config, status_monitor)`
3. Add it to the `AVAILABLE_APPS` dictionary in `src/run.py`

### Analyzing Results

- Simulation results: `storage/output/`
- Profiler results: `storage/prof5_results/`
- Execution logs: `storage/logs/`
- Error analysis: use `src/utils/error_analyzer.py`

### Tracking Variants

- Executed variants log: `executed.txt`
- Failure log: `failures.txt`
- Checkpoint for resumption: `checkpoint.txt`
- Detailed tracking: `src/database/variant_tracker.py`

---

## Control Files

- `executed.txt`: Variants already simulated
- `failures.txt`: Variants that failed
- `checkpoint.txt`: State for automatic resumption

---

## License

Academic project. All rights reserved.
