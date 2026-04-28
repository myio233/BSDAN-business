import math
import sys
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent
OBOS_DIR = ROOT_DIR / "obos"
if str(OBOS_DIR) not in sys.path:
    sys.path.insert(0, str(OBOS_DIR))

from exschool_game.engine import ExschoolSimulator, MAX_PRICE
from fit_weighted_theoretical_cpi_model import (
    EPS,
    STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT,
    STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD,
    apply_stage1_residual_calibration,
)


def test_runtime_cpi_default_aligns_with_stage1_residual_calibrator() -> None:
    threshold = STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD
    base_pred_log = np.log(np.array([threshold * 0.9, threshold * 5.0, threshold * 5.0], dtype=float))
    markets = np.array(["Hangzhou", "Hangzhou", "Shanghai"], dtype=object)
    rounds = np.array(["r3", f"r{STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD}", "r4"], dtype=object)
    prices = np.array([20_000.0, 20_000.0, 20_000.0], dtype=float)
    prev_marketshare_clean = np.array([0.2, 0.05, 0.2], dtype=float)
    prev_market_utilization_clean = np.array([0.6, 0.6, 0.6], dtype=float)

    legacy_log, legacy_shift, legacy_cpi = ExschoolSimulator._predict_runtime_cpi_from_log_predictions(
        base_pred_log,
        markets,
        prices,
        rounds=rounds,
        prev_marketshare_clean=prev_marketshare_clean,
        prev_market_utilization_clean=prev_market_utilization_clean,
        apply_residual_calibrator=False,
    )
    runtime_log, runtime_shift, runtime_cpi = ExschoolSimulator._predict_runtime_cpi_from_log_predictions(
        base_pred_log,
        markets,
        prices,
        rounds=rounds,
        prev_marketshare_clean=prev_marketshare_clean,
        prev_market_utilization_clean=prev_market_utilization_clean,
    )
    expected_log, expected_shift = apply_stage1_residual_calibration(
        base_pred_log,
        markets,
        rounds=rounds,
        prev_marketshare_clean=prev_marketshare_clean,
        prev_market_utilization_clean=prev_market_utilization_clean,
    )

    assert np.allclose(legacy_shift, np.zeros(3, dtype=float))
    assert runtime_shift[0] == 0.0
    assert runtime_shift[1] == expected_shift[1]
    assert runtime_shift[2] == 0.0
    assert np.allclose(runtime_log, expected_log)
    assert np.allclose(runtime_log, legacy_log + runtime_shift)
    assert runtime_cpi[0] == legacy_cpi[0]
    assert runtime_cpi[2] == legacy_cpi[2]
    assert runtime_cpi[1] > legacy_cpi[1]


def test_runtime_cpi_keeps_price_penalty_after_stage1_residual_calibration() -> None:
    threshold = STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD
    base_pred_log = np.log(np.array([threshold * 5.0, threshold * 5.0], dtype=float))
    markets = np.array(["Hangzhou", "Hangzhou"], dtype=object)
    rounds = np.array(["r3", f"r{STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD}"], dtype=object)
    prices = np.array([20_000.0, MAX_PRICE], dtype=float)
    prev_marketshare_clean = np.array([0.2, 0.2], dtype=float)
    prev_market_utilization_clean = np.array([0.6, 0.6], dtype=float)

    _runtime_log, runtime_shift, runtime_cpi = ExschoolSimulator._predict_runtime_cpi_from_log_predictions(
        base_pred_log,
        markets,
        prices,
        rounds=rounds,
        prev_marketshare_clean=prev_marketshare_clean,
        prev_market_utilization_clean=prev_market_utilization_clean,
    )
    expected_log, expected_shift = apply_stage1_residual_calibration(
        base_pred_log,
        markets,
        rounds=rounds,
        prev_marketshare_clean=prev_marketshare_clean,
        prev_market_utilization_clean=prev_market_utilization_clean,
    )
    calibrated_cpi = max(math.exp(expected_log[0]) - EPS, 0.0)
    penalized_source_cpi = max(math.exp(expected_log[1]) - EPS, 0.0)
    expected_penalized_cpi = penalized_source_cpi / 15.0

    assert np.allclose(runtime_shift, expected_shift)
    assert np.isclose(runtime_cpi[0], calibrated_cpi, rtol=1e-9, atol=1e-12)
    assert np.isclose(runtime_cpi[1], expected_penalized_cpi, rtol=1e-9, atol=1e-12)
    assert runtime_cpi[1] < runtime_cpi[0]
    assert np.isclose(runtime_shift[1], STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT)
