import math
import sys
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parent
OBOS_DIR = ROOT_DIR / "obos"

if str(OBOS_DIR) not in sys.path:
    sys.path.insert(0, str(OBOS_DIR))

from obos.fit_weighted_theoretical_cpi_model import (
    STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LATE_CHALLENGER_SHIFT_MULTIPLIER,
    STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_EXTRA_LOG_SLOPE,
    STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LATE_MAX_LOG_SHIFT,
    STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT,
    STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE,
    STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT,
    apply_stage1_residual_calibration,
)


def test_stage1_residual_calibration_only_hits_high_cpi_hangzhou_rows():
    threshold = STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD
    pred_log = np.log(np.array([threshold * 0.9, threshold * 1.5, threshold * 5.0], dtype=float))
    markets = ["Hangzhou", "Shanghai", "Hangzhou"]

    adjusted_log, residual_shift = apply_stage1_residual_calibration(pred_log, markets)

    assert residual_shift[0] == 0.0
    assert residual_shift[1] == 0.0

    expected_uncapped = STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE * math.log(5.0)
    expected_shift = min(expected_uncapped, STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT)
    assert residual_shift[2] == expected_shift
    assert adjusted_log[2] == pred_log[2] + expected_shift


def test_stage1_residual_calibration_reallocates_late_hangzhou_shift_by_lag_state():
    threshold = STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD
    pred_log = np.log(np.array([threshold * 5.0, threshold * 5.0, threshold * 5.0], dtype=float))
    markets = ["Hangzhou", "Hangzhou", "Hangzhou"]
    prev_marketshare_clean = np.array(
        [
            STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD + 0.01,
            STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD - 0.01,
            STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD + 0.01,
        ],
        dtype=float,
    )
    prev_market_utilization_clean = np.array(
        [
            STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD + 0.01,
            STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD + 0.01,
            STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD - 0.01,
        ],
        dtype=float,
    )

    adjusted_log, residual_shift = apply_stage1_residual_calibration(
        pred_log,
        markets,
        prev_marketshare_clean=prev_marketshare_clean,
        prev_market_utilization_clean=prev_market_utilization_clean,
    )

    high_cpi_log = math.log(5.0)
    base_shift = min(
        STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE * high_cpi_log,
        STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT,
    )
    expected_incumbent_shift = min(
        base_shift + STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_EXTRA_LOG_SLOPE * high_cpi_log,
        STAGE1_RESIDUAL_CALIBRATION_LATE_MAX_LOG_SHIFT,
    )
    expected_challenger_shift = base_shift * STAGE1_RESIDUAL_CALIBRATION_LATE_CHALLENGER_SHIFT_MULTIPLIER

    assert np.isclose(residual_shift[0], expected_incumbent_shift)
    assert np.isclose(residual_shift[1], expected_challenger_shift)
    assert np.isclose(residual_shift[2], base_shift)
    assert np.isclose(adjusted_log[0], pred_log[0] + expected_incumbent_shift)
    assert np.isclose(adjusted_log[1], pred_log[1] + expected_challenger_shift)


def test_stage1_residual_calibration_tightens_late_incumbent_cap_from_r4_onward():
    threshold = STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD
    pred_log = np.log(np.array([threshold * 5.0, threshold * 5.0], dtype=float))
    markets = ["Hangzhou", "Hangzhou"]
    rounds = ["r3", f"r{STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD}"]
    prev_marketshare_clean = np.array(
        [
            STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD + 0.01,
            STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD + 0.01,
        ],
        dtype=float,
    )
    prev_market_utilization_clean = np.array(
        [
            STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD + 0.01,
            STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD + 0.01,
        ],
        dtype=float,
    )

    adjusted_log, residual_shift = apply_stage1_residual_calibration(
        pred_log,
        markets,
        rounds=rounds,
        prev_marketshare_clean=prev_marketshare_clean,
        prev_market_utilization_clean=prev_market_utilization_clean,
    )

    high_cpi_log = math.log(5.0)
    uncapped_incumbent_shift = (
        min(
            STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE * high_cpi_log,
            STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT,
        )
        + STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_EXTRA_LOG_SLOPE * high_cpi_log
    )
    expected_pre_r4_shift = min(uncapped_incumbent_shift, STAGE1_RESIDUAL_CALIBRATION_LATE_MAX_LOG_SHIFT)
    expected_r4_shift = min(uncapped_incumbent_shift, STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT)

    assert np.isclose(residual_shift[0], expected_pre_r4_shift)
    assert np.isclose(residual_shift[1], expected_r4_shift)
    assert residual_shift[1] < residual_shift[0]
    assert np.isclose(adjusted_log[0], pred_log[0] + expected_pre_r4_shift)
    assert np.isclose(adjusted_log[1], pred_log[1] + expected_r4_shift)
