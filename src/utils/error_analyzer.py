import logging
import numpy as np
import warnings
import math

def safe_correlation(x, y):
    """
    Calcula a correlação de Pearson com tratamento robusto para casos especiais.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        try:
            if np.nanstd(x) == 0 or np.nanstd(y) == 0:
                return np.nan
            corr = np.corrcoef(x, y, rowvar=False)
            return corr[0, 1] if not np.isnan(corr[0, 1]) else np.nan
        except Exception as e:
            logging.warning(f"Não foi possível calcular a correlação: {e}")
            return np.nan

def _ensure_sequence(data):
    """Garante que `data` seja uma sequência indexável/lista.
    - Se for None -> lista vazia
    - Se tiver __len__ -> retorna como está
    - Se for iterável (gerador, map, etc.) -> converte para list
    - Caso contrário (valor escalar) -> embrulha em lista
    """
    if data is None:
        return []
    # Strings são sequências, mas geralmente não são esperadas aqui;
    # mantemos comportamento padrão (len válido) para não alterar demais.
    try:
        _ = len(data)
        return data
    except TypeError:
        # Não tem len: tentar iterar e transformar em lista
        try:
            return list(data)
        except Exception:
            # Não iterável: tratar como escalar
            return [data]

def calculate_metrics(exact_data, approx_data):
    """
    Calcula várias métricas de precisão entre os dados exatos e aproximados, lidando com NaN.
    """
    exact_seq = _ensure_sequence(exact_data)
    approx_seq = _ensure_sequence(approx_data)

    if len(exact_seq) != len(approx_seq):
        raise ValueError(f"Tamanhos incompatíveis: exact={len(exact_seq)}, approx={len(approx_seq)}")

    n = len(exact_seq)
    if n == 0:
        return {"count": 0, "mse": 0.0, "mae": 0.0, "max_error": 0.0}

    mse_acc = 0.0
    mae_acc = 0.0
    max_err = 0.0

    for i, (e, a) in enumerate(zip(exact_seq, approx_seq)):
        try:
            ev = float(e)
            av = float(a)
        except Exception as exc:
            raise ValueError(f"Valor não numérico na posição {i}: exact={e!r}, approx={a!r}") from exc

        err = ev - av
        abs_err = abs(err)
        mse_acc += err * err
        mae_acc += abs_err
        if abs_err > max_err:
            max_err = abs_err

    mse = mse_acc / n
    mae = mae_acc / n

    return {
        "count": n,
        "mse": mse,
        "mae": mae,
        "max_error": max_err
    }

def calculate_error(reference_output_path, variant_output_path):
    """
    Calcula a acurácia entre a saída de referência e a saída da variante.

    Lê os dados dos arquivos de saída, converte para arrays numpy e calcula métricas de erro.
    Retorna um valor de accuracy (float) no intervalo [0.0, 1.0]. Também registra as métricas calculadas.
    """
    try:
        ref_data = np.loadtxt(reference_output_path)
        var_data = np.loadtxt(variant_output_path)

        metrics = calculate_metrics(ref_data, var_data)

        # RMSE
        rmse = math.sqrt(metrics.get("mse", 0.0))

        # escala usada para normalização: máximo absoluto do dado de referência
        try:
            scale = float(np.nanmax(np.abs(ref_data)))
        except Exception:
            scale = 0.0

        if metrics.get("count", 0) == 0:
            accuracy = 1.0
        else:
            if scale == 0.0:
                accuracy = 1.0 if rmse == 0.0 else 0.0
            else:
                accuracy = 1.0 - (rmse / scale)
                # clamp
                accuracy = max(0.0, min(1.0, accuracy))

        logging.info(f"Métricas calculadas para {variant_output_path}: {metrics}")
        logging.debug(f"Accuracy calculada para {variant_output_path}: {accuracy}")
        return accuracy

    except FileNotFoundError as e:
        logging.error(f"Arquivo não encontrado ao calcular o erro para {variant_output_path}: {e}")
        return 0.0
    except ValueError as e:
        logging.error(f"Formato inválido ao calcular o erro para {variant_output_path}: {e}")
        return 0.0
    except Exception as e:
        logging.exception(f"Erro inesperado ao calcular o erro para {variant_output_path}: {e}")
        return 0.0