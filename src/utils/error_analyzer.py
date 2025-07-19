import logging
import numpy as np
import warnings

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

def calculate_metrics(exact_data, approx_data, error_threshold=1e-5):
    """
    Calcula várias métricas de precisão entre os dados exatos e aproximados, lidando com NaN.
    """
    if len(exact_data) != len(approx_data):
        msg = f'Tamanhos diferentes: exato={len(exact_data)}, aprox={len(approx_data)}'
        logging.warning(msg)
        return {'ERROR': msg}

    valid_mask = ~(np.isnan(exact_data) | np.isnan(approx_data))
    if not np.any(valid_mask):
        msg = 'Nenhum dado válido para comparação'
        logging.warning(msg)
        return {'ERROR': msg}

    exact_valid = exact_data[valid_mask]
    approx_valid = approx_data[valid_mask]

    absolute_error = np.abs(exact_valid - approx_valid)
    miss_mask = absolute_error > error_threshold
    miss_rate = np.mean(miss_mask)

    relative_error = np.zeros_like(absolute_error)
    non_zero_mask = exact_valid != 0
    if np.any(non_zero_mask):
        relative_error[non_zero_mask] = absolute_error[non_zero_mask] / np.abs(exact_valid[non_zero_mask])

    exact_range = np.max(exact_valid) - np.min(exact_valid) if np.any(valid_mask) else 0
    nrmse = np.sqrt(np.mean(absolute_error**2)) / exact_range if exact_range > 0 else np.nan

    return {
        'MAE': float(np.mean(absolute_error)),
        'MSE': float(np.mean(absolute_error**2)),
        'RMSE': float(np.sqrt(np.mean(absolute_error**2))),
        'MRE': float(np.mean(relative_error[non_zero_mask])) if np.any(non_zero_mask) else 0.0,
        'MAX_ERROR': float(np.max(absolute_error)),
        'NRMSE': float(nrmse) if not np.isnan(nrmse) else None,
        'CORRELATION': float(safe_correlation(exact_valid, approx_valid)),
        'ACCURACY': float(1.0 - np.mean(miss_mask)),
        'MISS_RATE': float(miss_rate),
        'VALID_POINTS': int(np.sum(valid_mask)),
        'TOTAL_POINTS': len(exact_data)
    }

def calculate_error(reference_output_path, variant_output_path):
    """
    Calcula a acurácia entre a saída de referência e a saída da variante.

    Lê os dados dos arquivos de saída, converte para arrays numpy e calcula a acurácia.

    Retorna a acurácia (um float entre 0 e 1) ou None se o cálculo falhar.
    """
    try:
        ref_data = np.loadtxt(reference_output_path)
        var_data = np.loadtxt(variant_output_path)

        metrics = calculate_metrics(ref_data, var_data)

        if 'ERROR' in metrics:
            logging.warning(f"Não foi possível calcular a acurácia para {variant_output_path}: {metrics['ERROR']}")
            return None
        
        accuracy = metrics.get('ACCURACY')
        if accuracy is not None:
            logging.info(f"Acurácia calculada para {variant_output_path}: {accuracy:.4f}")
        
        return accuracy

    except (FileNotFoundError, ValueError) as e:
        logging.error(f"Não foi possível calcular o erro para {variant_output_path}: {e}")
        return None