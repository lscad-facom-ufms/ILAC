"""
MetricsCollector - Módulo para coletar e comparar métricas de variantes

Este módulo coleta métricas de cada variante e compara com a versão original.
Métricas coletadas:
- Erro (ARE/RMSE/Miss Rate)
- Energia (Joules)
- Potência (Watts)
- Tempo/Latência (ms)
- IPC (Instruções por Ciclo)
- Linhas modificadas
- Contagem de instruções
"""

import os
import json
import glob
import re
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class VariantMetrics:
    """Métricas de uma variante."""
    variant_hash: str
    variant_type: str  # "original" ou "approximate"
    
    # Métricas de erro
    error: Optional[float] = None
    error_type: Optional[str] = None  # "ARE", "RMSE", "MissRate", etc.
    
    # Métricas de energia/tempo
    energy_joules: Optional[float] = None
    power_watts: Optional[float] = None
    latency_ms: Optional[float] = None
    execution_time_ms: Optional[float] = None
    
    # Métricas de performance
    total_instructions: Optional[int] = None
    ipc: Optional[float] = None
    cycles: Optional[int] = None
    
    # Informações do código
    modified_lines: List[int] = field(default_factory=list)
    num_modified_lines: int = 0
    
    # Arquivos
    output_file: Optional[str] = None
    prof5_file: Optional[str] = None
    time_file: Optional[str] = None
    error_file: Optional[str] = None
    spike_log: Optional[str] = None


class MetricsCollector:
    """Coletor de métricas para análise de variantes."""
    
    def __init__(self, app_name: str, workspace: str = "storage"):
        self.app_name = app_name
        # Se workspace inclui subdiretório (execution), usa diretamente
        # Caso contrário, usa a estrutura padrão
        self.workspace = workspace
        self.variants: Dict[str, VariantMetrics] = {}
        self.original_variant: Optional[VariantMetrics] = None
    
    def _get_dirs(self) -> Dict[str, str]:
        """Retorna os diretórios do workspace."""
        # Verifica se é um workspace de execução (contém 'executions')
        if "executions" in self.workspace:
            base = self.workspace
        else:
            base = self.workspace
        
        return {
            "outputs": os.path.join(base, "outputs"),
            "prof5": os.path.join(base, "prof5_results"),
            "logs": os.path.join(base, "logs"),
            "linhas": os.path.join(base, "linhas_modificadas"),
        }
    
    def _find_files(self, pattern: str, directory: str) -> List[str]:
        """Encontra arquivos que matching um padrão."""
        if not os.path.exists(directory):
            return []
        return glob.glob(os.path.join(directory, pattern))
    
    def _read_json(self, filepath: str) -> Optional[Dict]:
        """Lê arquivo JSON."""
        try:
            with open(filepath, 'r') as f:
                return json.load(f)
        except:
            return None
    
    def _read_float_file(self, filepath: str) -> Optional[float]:
        """Lê arquivo com um float."""
        try:
            with open(filepath, 'r') as f:
                return float(f.read().strip())
        except:
            return None
    
    def _read_int_file(self, filepath: str) -> Optional[int]:
        """Lê arquivo com um int."""
        try:
            with open(filepath, 'r') as f:
                return int(f.read().strip())
        except:
            return None
    
    def _parse_spike_log(self, log_file: str) -> Dict:
        """Extrai métricas do log do Spike."""
        metrics = {
            "total_instructions": None,
            "ipc": None,
            "cycles": None,
        }
        
        try:
            with open(log_file, 'r') as f:
                content = f.read()
            
            # Procura por instruções totais
            inst_match = re.search(r'total\s+instructions\s+\[(\d+)\]', content, re.IGNORECASE)
            if inst_match:
                metrics["total_instructions"] = int(inst_match.group(1))
            
            # Procura por ciclos
            cycles_match = re.search(r'total\s+cycles\s+\[(\d+)\]', content, re.IGNORECASE)
            if cycles_match:
                metrics["cycles"] = int(cycles_match.group(1))
            
            # Calcula IPC
            if metrics["total_instructions"] and metrics["cycles"]:
                metrics["ipc"] = metrics["total_instructions"] / metrics["cycles"]
                
        except:
            pass
        
        return metrics
    
    def _get_exe_prefix(self) -> str:
        """Retorna o prefixo do executável baseado no app."""
        prefixes = {
            "blackscholes": "blackscholes_",
            "inversek2j": "inversek2j_",
            "jmeint": "jmeint_",
            "fft": "fourier_",
            "sobel": "sobel_",
            "kmeans": "kmeans_",
        }
        return prefixes.get(self.app_name, f"{self.app_name}_")
    
    def collect_all_variants(self) -> List[VariantMetrics]:
        """Coleta métricas de todas as variantes."""
        dirs = self._get_dirs()
        exe_prefix = self._get_exe_prefix()
        
        # Procura por todos os arquivos de saída
        output_files = self._find_files(f"{exe_prefix}*.data", dirs["outputs"]) + \
                      self._find_files(f"{exe_prefix}*.rgb", dirs["outputs"]) + \
                      self._find_files(f"{exe_prefix}*.csv", dirs["outputs"])
        
        for output_file in output_files:
            filename = os.path.basename(output_file)
            
            # Extrai o hash do nome do arquivo
            hash_match = re.search(f'{exe_prefix}([a-fA-F0-9]+)', filename)
            if not hash_match:
                continue
            
            variant_hash = hash_match.group(1)
            variant_type = "original" if variant_hash == "original" or len(variant_hash) <= 8 else "approximate"
            
            metrics = VariantMetrics(
                variant_hash=variant_hash,
                variant_type=variant_type,
                output_file=output_file
            )
            
            # Procura arquivos relacionados
            base_name = os.path.splitext(output_file)[0]
            
            # Arquivo de erro
            error_file = base_name + ".error"
            if os.path.exists(error_file):
                metrics.error_file = error_file
                metrics.error = self._read_float_file(error_file)
            
            # Arquivo de tempo
            time_file = base_name + ".time"
            if os.path.exists(time_file):
                metrics.time_file = time_file
                metrics.execution_time_ms = self._read_float_file(time_file)
            
            # Arquivo .prof5 (pode ter extensão diferente)
            prof5_files = self._find_files(f"*{variant_hash}*.prof5", dirs["prof5"])
            if prof5_files:
                metrics.prof5_file = prof5_files[0]
                prof5_data = self._read_json(prof5_files[0])
                if prof5_data:
                    summary = prof5_data.get("summary", {})
                    
                    # Energia (uW_s -> J)
                    energy_us = summary.get("energy_uW_s") or summary.get("energy_total")
                    if energy_us:
                        metrics.energy_joules = energy_us / 1e6  # uW*s -> J
                    
                    # Potência (avg_power já é em W)
                    metrics.power_watts = summary.get("avg_power") or summary.get("power")
                    
                    # Latência
                    metrics.latency_ms = summary.get("latency_ms")
                    
                    # IPC
                    metrics.ipc = summary.get("IPC")
                    
                    # Ciclos e instruções
                    metrics.cycles = summary.get("cycles")
                    metrics.total_instructions = summary.get("Total_Inst_Mapped") or summary.get("Total_Inst_Log")
            
            # Log do Spike
            spike_log = os.path.join(dirs["logs"], f"{exe_prefix}{variant_hash}.log")
            if os.path.exists(spike_log):
                metrics.spike_log = spike_log
                spike_metrics = self._parse_spike_log(spike_log)
                metrics.total_instructions = spike_metrics.get("total_instructions")
                metrics.cycles = spike_metrics.get("cycles")
                metrics.ipc = spike_metrics.get("ipc")
            
            # Linhas modificadas
            linhas_files = self._find_files(f"linhas_{variant_hash}.txt", dirs["linhas"])
            if linhas_files:
                try:
                    with open(linhas_files[0], 'r') as f:
                        metrics.modified_lines = [int(line.strip()) for line in f if line.strip()]
                        metrics.num_modified_lines = len(metrics.modified_lines)
                except:
                    pass
            
            # Armazena a variante
            self.variants[variant_hash] = metrics
            
            if variant_type == "original":
                self.original_variant = metrics
        
        return list(self.variants.values())
    
    def compare_with_original(self) -> List[Dict]:
        """Compara cada variante com a versão original."""
        if not self.original_variant:
            self.collect_all_variants()
        
        if not self.original_variant:
            return []
        
        original = self.original_variant
        comparisons = []
        
        for variant_hash, metrics in self.variants.items():
            if variant_hash == "original" or len(variant_hash) <= 8:
                continue
            
            comp = {
                "variant_hash": variant_hash,
                "variant_type": "approximate",
            }
            
            # Erro
            if metrics.error is not None:
                comp["error"] = metrics.error
                comp["error_%"] = metrics.error * 100 if metrics.error <= 1 else metrics.error
            
            # Energia
            if metrics.energy_joules and original.energy_joules:
                comp["energy_joules"] = metrics.energy_joules
                comp["energy_saved_%"] = ((original.energy_joules - metrics.energy_joules) / original.energy_joules) * 100
            
            # Potência
            if metrics.power_watts and original.power_watts:
                comp["power_watts"] = metrics.power_watts
                comp["power_saved_%"] = ((original.power_watts - metrics.power_watts) / original.power_watts) * 100
            
            # Latência
            if metrics.latency_ms and original.latency_ms:
                comp["latency_ms"] = metrics.latency_ms
                comp["latency_change_%"] = ((metrics.latency_ms - original.latency_ms) / original.latency_ms) * 100
            
            # IPC
            if metrics.ipc and original.ipc:
                comp["ipc"] = metrics.ipc
                comp["ipc_change_%"] = ((metrics.ipc - original.ipc) / original.ipc) * 100
            
            # Instruções
            if metrics.total_instructions and original.total_instructions:
                comp["total_instructions"] = metrics.total_instructions
                comp["instructions_change_%"] = ((metrics.total_instructions - original.total_instructions) / original.total_instructions) * 100
            
            # Linhas modificadas
            comp["num_modified_lines"] = metrics.num_modified_lines
            
            comparisons.append(comp)
        
        return comparisons
    
    def generate_report(self, output_file: str = "metrics_report.json") -> str:
        """Gera um relatório completo em JSON com dados brutos e comparações."""
        if not self.variants:
            self.collect_all_variants()
        
        return self._create_report_json(output_file)
    
    def _create_report_json(self, output_file: str = None) -> str:
        """Cria o dicionário do relatório em JSON."""
        if not self.variants:
            self.collect_all_variants()
        
        report = {
            "app_name": self.app_name,
            "generated_at": datetime.now().isoformat(),
            "total_variants": len(self.variants),
            "original_variant": None,
            "approximate_variants": [],
            "comparisons": self.compare_with_original()
        }
        
        # Adiciona dados da versão original
        if self.original_variant:
            orig = self.original_variant
            report["original_variant"] = {
                "hash": orig.variant_hash,
                "error": orig.error,
                "energy_joules": orig.energy_joules,
                "power_watts": orig.power_watts,
                "latency_ms": orig.latency_ms,
                "ipc": orig.ipc,
                "total_instructions": orig.total_instructions,
                "cycles": orig.cycles,
                "num_modified_lines": orig.num_modified_lines,
            }
        
        # Adiciona todas as variantes (dados brutos)
        for v_hash, metrics in self.variants.items():
            variant_data = {
                "hash": v_hash,
                "type": metrics.variant_type,
                "error": metrics.error,
                "error_type": metrics.error_type,
                "energy_joules": metrics.energy_joules,
                "power_watts": metrics.power_watts,
                "latency_ms": metrics.latency_ms,
                "execution_time_ms": metrics.execution_time_ms,
                "ipc": metrics.ipc,
                "total_instructions": metrics.total_instructions,
                "cycles": metrics.cycles,
                "num_modified_lines": metrics.num_modified_lines,
            }
            if metrics.variant_type == "original":
                report["original_variant"] = variant_data
            else:
                report["approximate_variants"].append(variant_data)
        
        # Adiciona estatísticas resumidas
        report["statistics"] = self._calculate_statistics()
        
        return report
    
    def _calculate_statistics(self) -> Dict:
        """Calcula estatísticas resumidas das variantes."""
        if not self.variants:
            return {}
        
        comparisons = self.compare_with_original()
        if not comparisons:
            return {}
        
        stats = {
            "total_variants": len(comparisons)
        }
        
        # Coleta valores para cálculo
        errors = [c.get("error") for c in comparisons if c.get("error") is not None]
        energy_savings = [c.get("energy_saved_%") for c in comparisons if c.get("energy_saved_%") is not None]
        power_savings = [c.get("power_saved_%") for c in comparisons if c.get("power_saved_%") is not None]
        latency_changes = [c.get("latency_change_%") for c in comparisons if c.get("latency_change_%") is not None]
        
        # Erro
        if errors:
            stats["error"] = {
                "min": min(errors),
                "max": max(errors),
                "avg": sum(errors) / len(errors)
            }
        
        # Economia de energia
        if energy_savings:
            stats["energy_saved_%"] = {
                "min": min(energy_savings),
                "max": max(energy_savings),
                "avg": sum(energy_savings) / len(energy_savings)
            }
        
        # Economia de potência
        if power_savings:
            stats["power_saved_%"] = {
                "min": min(power_savings),
                "max": max(power_savings),
                "avg": sum(power_savings) / len(power_savings)
            }
        
        # Mudança de latência
        if latency_changes:
            stats["latency_change_%"] = {
                "min": min(latency_changes),
                "max": max(latency_changes),
                "avg": sum(latency_changes) / len(latency_changes)
            }
        
        # Melhor variante (maior economia de energia)
        if energy_savings:
            best_idx = energy_savings.index(max(energy_savings))
            stats["best_variant"] = {
                "hash": comparisons[best_idx]["variant_hash"],
                "energy_saved_%": energy_savings[best_idx],
                "error": comparisons[best_idx].get("error")
            }
        
        return stats
    
    def save_accumulated_report(self, output_file: str, execution_params: Dict = None) -> str:
        """
        Salva o relatório acumulando com execuções anteriores.
        Se o arquivo já existir, carrega e adiciona a nova execução.
        """
        # Coleta métricas atuais
        current_report = self._create_report_json()
        
        # Adiciona parâmetros da execução
        if execution_params:
            current_report["execution_params"] = execution_params
        
        # Tenta carregar relatório existente
        existing_report = None
        if os.path.exists(output_file):
            try:
                with open(output_file, 'r') as f:
                    existing_report = json.load(f)
            except:
                pass
        
        if existing_report and "executions" in existing_report:
            # Adiciona nova execução à lista
            existing_report["executions"].append(current_report)
            existing_report["total_executions"] = len(executions := existing_report["executions"])
            existing_report["updated_at"] = datetime.now().isoformat()
            
            # Atualiza estatísticas combinadas
            existing_report["summary"] = self._calculate_combined_summary(existing_report["executions"])
            
            report = existing_report
        else:
            # Primeiro relatório ou formato antigo
            report = {
                "app_name": self.app_name,
                "total_executions": 1,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "executions": [current_report],
                "summary": self._calculate_combined_summary([current_report])
            }
        
        # Salva relatório atualizado
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        return output_file
    
    def _calculate_combined_summary(self, executions: List[Dict]) -> Dict:
        """Calcula estatísticas combinadas de todas as execuções."""
        all_variants = []
        all_errors = []
        all_energy_savings = []
        
        for exec_data in executions:
            # Coleta de comparisons ou approximate_variants
            variants = exec_data.get("comparisons", [])
            for v in variants:
                all_variants.append(v)
                if v.get("error") is not None:
                    all_errors.append(v["error"])
                if v.get("energy_saved_%") is not None:
                    all_energy_savings.append(v["energy_saved_%"])
        
        summary = {
            "total_variants_tested": len(all_variants)
        }
        
        if all_errors:
            summary["error"] = {
                "min": min(all_errors),
                "max": max(all_errors),
                "avg": sum(all_errors) / len(all_errors)
            }
        
        if all_energy_savings:
            summary["energy_saved_%"] = {
                "min": min(all_energy_savings),
                "max": max(all_energy_savings),
                "avg": sum(all_energy_savings) / len(all_energy_savings)
            }
            # Melhor economia geral
            best_idx = all_energy_savings.index(max(all_energy_savings))
            if best_idx < len(all_variants):
                summary["best_overall"] = {
                    "hash": all_variants[best_idx].get("variant_hash", "unknown"),
                    "energy_saved_%": all_energy_savings[best_idx]
                }
        
        return summary
        for v_hash, metrics in self.variants.items():
            variant_data = {
                "hash": v_hash,
                "type": metrics.variant_type,
                # Erro
                "error": metrics.error,
                "error_type": metrics.error_type,
                # Energia
                "energy_joules": metrics.energy_joules,
                "power_watts": metrics.power_watts,
                # Tempo
                "latency_ms": metrics.latency_ms,
                "execution_time_ms": metrics.execution_time_ms,
                # Performance
                "ipc": metrics.ipc,
                "total_instructions": metrics.total_instructions,
                "cycles": metrics.cycles,
                # Código
                "num_modified_lines": metrics.num_modified_lines,
                "modified_lines": metrics.modified_lines,
                # Arquivos
                "output_file": metrics.output_file,
                "prof5_file": metrics.prof5_file,
                "time_file": metrics.time_file,
                "error_file": metrics.error_file,
                "spike_log": metrics.spike_log,
            }
            if metrics.variant_type == "original":
                report["original_variant"] = variant_data
            else:
                report["approximate_variants"].append(variant_data)
        
        # Salva relatório
        with open(output_file, 'w') as f:
            json.dump(report, f, indent=2)
        
        return output_file
    
    def print_summary(self) -> None:
        """Imprime um resumo no console."""
        if not self.variants:
            self.collect_all_variants()
        
        comparisons = self.compare_with_original()
        
        print(f"\n{'='*80}")
        print(f"Métricas da Aplicação: {self.app_name}")
        print(f"{'='*80}")
        
        # Original
        if self.original_variant:
            orig = self.original_variant
            print(f"\n[VERSÃO ORIGINAL]")
            print(f"  Hash: {orig.variant_hash}")
            print(f"  Energia: {orig.energy_joules:.6f} J" if orig.energy_joules else "  Energia: N/A")
            print(f"  Potência: {orig.power_watts:.6f} W" if orig.power_watts else "  Potência: N/A")
            print(f"  Latência: {orig.latency_ms:.2f} ms" if orig.latency_ms else "  Latência: N/A")
            print(f"  IPC: {orig.ipc:.4f}" if orig.ipc else "  IPC: N/A")
            print(f"  Instruções: {orig.total_instructions}" if orig.total_instructions else "  Instruções: N/A")
        
        # Tabela comparativa
        if comparisons:
            print(f"\n[COMPARAÇÃO DAS VARIANTES APROXIMADAS]")
            print(f"{'-'*80}")
            print(f"{'Hash':<12} {'Erro %':<12} {'Energia %':<14} {'Lat%':<10} {'IPC':<10} {'Linhas':<8}")
            print(f"{'-'*80}")
            
            for comp in comparisons:
                error_str = f"{comp.get('error_%', 0):.2f}%" if comp.get('error') is not None else "N/A"
                energy_str = f"{comp.get('energy_saved_%', 0):.2f}%" if comp.get('energy_saved_%') is not None else "N/A"
                latency_str = f"{comp.get('latency_change_%', 0):+.2f}%" if comp.get('latency_change_%') is not None else "N/A"
                ipc_str = f"{comp.get('ipc', 0):.4f}" if comp.get('ipc') is not None else "N/A"
                linhas_str = str(comp.get('num_modified_lines', 0))
                
                print(f"{comp['variant_hash']:<12} {error_str:<12} {energy_str:<14} {latency_str:<10} {ipc_str:<10} {linhas_str:<8}")
        
        print(f"\nTotal de variantes aproximadas: {len(comparisons)}")
        print(f"{'='*80}\n")


def collect_metrics(app_name: str, workspace: str = "storage") -> MetricsCollector:
    """Função de conveniência para coletar métricas."""
    collector = MetricsCollector(app_name, workspace)
    collector.collect_all_variants()
    return collector


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("Uso: python -m src.utils.metrics_collector <app_name> [workspace]")
        print("Exemplo: python -m src.utils.metrics_collector blackscholes")
        sys.exit(1)
    
    app_name = sys.argv[1]
    workspace = sys.argv[2] if len(sys.argv) > 2 else "storage"
    
    collector = MetricsCollector(app_name, workspace)
    collector.collect_all_variants()
    collector.print_summary()
    
    # Gera relatório JSON
    report_file = f"metrics_report_{app_name}.json"
    collector.generate_report(report_file)
    print(f"Relatório salvo em: {report_file}")
