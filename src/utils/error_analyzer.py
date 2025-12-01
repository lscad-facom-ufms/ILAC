import warnings
import math
import json
import logging
from pathlib import Path
from typing import Iterable, List, Any

def safe_correlation(x, y):
    """
    Calcula correlação de Pearson (fallback simples) com tratamento de erros.
    Retorna None se não for possível calcular.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            x_seq = _ensure_sequence(x)
            y_seq = _ensure_sequence(y)
            n = min(len(x_seq), len(y_seq))
            if n == 0:
                return None
            x_vals = [float(v) for v in x_seq[:n]]
            y_vals = [float(v) for v in y_seq[:n]]
            mean_x = sum(x_vals) / n
            mean_y = sum(y_vals) / n
            num = sum((a - mean_x) * (b - mean_y) for a, b in zip(x_vals, y_vals))
            den_x = math.sqrt(sum((a - mean_x) ** 2 for a in x_vals))
            den_y = math.sqrt(sum((b - mean_y) ** 2 for b in y_vals))
            denom = den_x * den_y
            if denom == 0:
                return None
            return num / denom
        except Exception:
            logging.exception("safe_correlation falhou")
            return None

def _ensure_sequence(data: Any) -> List[Any]:
    """Garante que `data` seja uma lista de elementos indexáveis."""
    if data is None:
        return []
    if isinstance(data, (list, tuple)):
        return list(data)
    # strings: mantemos como única linha (não quebrar em chars)
    if isinstance(data, str):
        return [data]
    try:
        # detecta objeto com __len__
        if hasattr(data, '__len__'):
            return list(data)
    except Exception:
        pass
    # iteráveis (geradores, map, etc.)
    try:
        return list(data)
    except Exception:
        # valor escalar
        return [data]

def calculate_metrics(exact_data: Iterable[float], approx_data: Iterable[float]) -> dict:
    """
    Calcula métricas element-wise entre exact_data e approx_data.
    Retorna dict com: count, mse, mae, max_error, mare (mean abs relative error), accuracy.
    """
    exact_seq = _ensure_sequence(exact_data)
    approx_seq = _ensure_sequence(approx_data)
    n = min(len(exact_seq), len(approx_seq))
    if n == 0:
        return {"count": 0, "mse": 0.0, "mae": 0.0, "max_error": 0.0, "mare": None, "accuracy": 0.0}

    sum_sq = 0.0
    sum_abs = 0.0
    sum_rel = 0.0
    max_err = 0.0
    eps = 1e-12

    for a_raw, b_raw in zip(exact_seq[:n], approx_seq[:n]):
        try:
            a = float(a_raw)
            b = float(b_raw)
        except Exception:
            # se não for convertível, pula o par
            n -= 1
            continue
        diff = b - a
        absdiff = abs(diff)
        sum_sq += diff * diff
        sum_abs += absdiff
        denom = abs(a) + eps
        sum_rel += absdiff / denom
        if absdiff > max_err:
            max_err = absdiff

    if n <= 0:
        return {"count": 0, "mse": 0.0, "mae": 0.0, "max_error": 0.0, "mare": None, "accuracy": 0.0}

    mse = sum_sq / n
    mae = sum_abs / n
    mare = sum_rel / n  # mean absolute relative error
    # definição simples de acurácia: 1 - MARE, truncado a [0,1]
    accuracy = max(0.0, 1.0 - mare)

    return {
        "count": n,
        "mse": mse,
        "mae": mae,
        "max_error": max_err,
        "mare": mare,
        "accuracy": accuracy
    }

def calculate_error(output_path: str, reference_path: str) -> dict:
    """
    Lê os arquivos (texto ou JSON) em output_path e reference_path,
    extrai seqüências numéricas e calcula métricas via calculate_metrics.
    Salva um arquivo JSON com sufixo .error.json ao lado do output_path e retorna o dict de métricas.
    """
    outp = Path(output_path)
    refp = Path(reference_path)
    logging.info(f"[error_analyzer] calculate_error called with output={outp} reference={refp}")

    if not outp.exists():
        logging.warning(f"[error_analyzer] output file not found: {outp}")
        return {"count": 0, "mse": 0.0, "mae": 0.0, "max_error": 0.0, "mare": None, "accuracy": 0.0}
    if not refp.exists():
        logging.warning(f"[error_analyzer] reference file not found: {refp}")
        return {"count": 0, "mse": 0.0, "mae": 0.0, "max_error": 0.0, "mare": None, "accuracy": 0.0}

    def _read_numbers(p: Path) -> List[float]:
        try:
            text = p.read_text(encoding='utf-8')
        except Exception:
            try:
                text = p.read_text(encoding='latin-1')
            except Exception:
                logging.exception(f"[error_analyzer] falha ao ler {p}")
                return []

        # tenta JSON primeiro
        try:
            data = json.loads(text)
            # aceita lista aninhada, dicionário com valores numéricos, etc.
            if isinstance(data, list):
                # achata listas recursivamente e extrai números
                nums = []
                def _flatten(obj):
                    if isinstance(obj, (list, tuple)):
                        for it in obj:
                            _flatten(it)
                    elif isinstance(obj, dict):
                        for v in obj.values():
                            _flatten(v)
                    else:
                        try:
                            nums.append(float(obj))
                        except Exception:
                            pass
                _flatten(data)
                return nums
            elif isinstance(data, dict):
                # extrai valores
                nums = []
                for v in data.values():
                    try:
                        nums.append(float(v))
                    except Exception:
                        pass
                return nums
            else:
                # valor escalar
                try:
                    return [float(data)]
                except Exception:
                    return []
        except Exception:
            pass

        # fallback: extrair números por regex / split
        parts = []
        for line in text.splitlines():
            for token in line.strip().split():
                try:
                    parts.append(float(token))
                except Exception:
                    # tenta remover vírgulas/ponteiros como "1,234" ou "1.234,"
                    tok = token.strip().strip(' ,;')
                    try:
                        parts.append(float(tok))
                    except Exception:
                        continue
        return parts

    ref_nums = _read_numbers(refp)
    out_nums = _read_numbers(outp)

    metrics = calculate_metrics(ref_nums, out_nums)

    # salva métricas ao lado do arquivo de output para auditoria
    try:
        metrics_path = outp.with_suffix(outp.suffix + ".error.json")
        metrics_path.write_text(json.dumps(metrics, indent=2), encoding='utf-8')
        logging.info(f"[error_analyzer] metrics saved to {metrics_path}")
    except Exception:
        logging.exception("[error_analyzer] falha ao salvar metrics")

    return metrics