from __future__ import annotations

import numpy as np
import pandas as pd

from exschool_game.modeling import (
    build_cpi_to_share_feature_matrix,
    predict_share_from_cpi_model,
)


class _ZeroDeltaRegressor:
    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        return np.zeros(len(X), dtype=float)


class _ConstantDeltaRegressor:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        return np.full(len(X), self.value, dtype=float)


def test_stage2_feature_matrix_exposes_late_hangzhou_gate_columns() -> None:
    rows = pd.DataFrame(
        [
            {
                "competition": "OBOS",
                "round": "r4",
                "team": "9",
                "market": "Hangzhou",
                "market_size": 43000.0,
                "sales_volume": 11554.0,
                "market_utilization_clean": 0.60,
                "prev_market_utilization_clean": 0.53,
                "prev_marketshare_clean": 0.169376,
                "m_rank_pct": 0.9,
                "q_rank_pct": 0.9,
                "mi_rank_pct": 0.9,
                "price_rank_pct": 0.2,
            },
            {
                "competition": "OBOS",
                "round": "r3",
                "team": "9",
                "market": "Hangzhou",
                "market_size": 39250.0,
                "sales_volume": 6648.0,
                "market_utilization_clean": 0.53,
                "prev_market_utilization_clean": 0.33,
                "prev_marketshare_clean": 0.000895,
                "m_rank_pct": 0.9,
                "q_rank_pct": 0.9,
                "mi_rank_pct": 0.9,
                "price_rank_pct": 0.2,
            },
        ]
    )

    feature_matrix = build_cpi_to_share_feature_matrix(rows, np.array([0.15, 0.16], dtype=float))

    assert np.allclose(feature_matrix["prev_marketshare_raw"], [0.169376, 0.000895])
    assert np.allclose(feature_matrix["late_round_gate"], [1.0, 1.0])
    assert np.allclose(feature_matrix["is_hangzhou_market"], [1.0, 1.0])
    assert np.allclose(feature_matrix["late_hangzhou_gate"], [1.0, 1.0])
    assert np.allclose(feature_matrix["late_hangzhou_r4_gate"], [1.0, 0.0])
    assert np.allclose(feature_matrix["late_hangzhou_prev_share"], [0.169376, 0.000895])


def test_predict_share_from_cpi_model_applies_late_hangzhou_residual_only_to_gated_row() -> None:
    share_X = pd.DataFrame(
        {
            "late_hangzhou_r4_gate": [1.0, 1.0, 0.0],
            "prev_marketshare_raw": [0.169376, 0.169376, 0.169376],
        }
    )
    share_model = {
        "estimator": _ZeroDeltaRegressor(),
        "columns": share_X.columns.tolist(),
        "mode": "delta_over_cpi",
        "late_hangzhou_residual_calibrator": {
            "gate_column": "late_hangzhou_r4_gate",
            "prev_share_column": "prev_marketshare_raw",
            "cpi_threshold": 0.12,
            "prev_share_threshold": 0.12,
            "share_ceiling_multiplier": 1.6,
            "prev_share_slope": 0.25,
            "max_adjustment": 0.05,
        },
    }

    share_pred, share_delta = predict_share_from_cpi_model(
        share_model,
        share_X,
        np.array([0.226378, 0.28, 0.226378], dtype=float),
    )

    assert np.allclose(share_pred, [0.268722, 0.28, 0.226378])
    assert np.allclose(share_delta, [0.042344, 0.0, 0.0])


def test_predict_share_from_cpi_model_skips_late_hangzhou_residual_once_base_share_is_above_comparison_ceiling() -> None:
    share_X = pd.DataFrame(
        {
            "late_hangzhou_r4_gate": [1.0],
            "prev_marketshare_raw": [0.169376],
        }
    )
    share_model = {
        "estimator": _ConstantDeltaRegressor(0.12),
        "columns": share_X.columns.tolist(),
        "mode": "delta_over_cpi",
        "late_hangzhou_residual_calibrator": {
            "gate_column": "late_hangzhou_r4_gate",
            "prev_share_column": "prev_marketshare_raw",
            "cpi_threshold": 0.12,
            "prev_share_threshold": 0.12,
            "share_ceiling_multiplier": 1.6,
            "prev_share_slope": 0.25,
            "max_adjustment": 0.05,
        },
    }

    share_pred, share_delta = predict_share_from_cpi_model(
        share_model,
        share_X,
        np.array([0.16], dtype=float),
    )

    assert np.allclose(share_pred, [0.28])
    assert np.allclose(share_delta, [0.12])
