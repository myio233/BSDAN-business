from __future__ import annotations

import numpy as np
import pandas as pd

from exschool_game.modeling import build_cpi_to_share_feature_matrix


def _base_stage2_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "13",
                "market": "Shanghai",
                "market_size": 120000.0,
                "sales_volume": 3000.0,
                "market_utilization_clean": 0.70,
                "prev_market_utilization_clean": 0.60,
                "m_rank_pct": 0.90,
                "q_rank_pct": 0.80,
                "mi_rank_pct": 0.85,
                "price_rank_pct": 0.25,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "7",
                "market": "Wuhan",
                "market_size": 32500.0,
                "sales_volume": 900.0,
                "market_utilization_clean": 0.55,
                "prev_market_utilization_clean": 0.50,
                "m_rank_pct": 0.40,
                "q_rank_pct": 0.35,
                "mi_rank_pct": 0.45,
                "price_rank_pct": 0.70,
            },
        ]
    )


def test_stage2_feature_matrix_adds_market_context_terms() -> None:
    rows = _base_stage2_rows().assign(
        population=[6_000_000.0, 2_500_000.0],
        penetration=[0.020, 0.013],
    )
    cpi_pred = np.array([0.08, 0.02], dtype=float)

    feature_matrix = build_cpi_to_share_feature_matrix(rows, cpi_pred)

    assert np.allclose(feature_matrix["population_log"], np.log1p(rows["population"]))
    assert np.allclose(feature_matrix["penetration_raw"], rows["penetration"])
    assert np.allclose(feature_matrix["market_size_log"], np.log1p(rows["market_size"]))
    assert np.allclose(feature_matrix["predicted_cpi_x_penetration"], cpi_pred * rows["penetration"])


def test_stage2_feature_matrix_defaults_missing_market_context_to_zero() -> None:
    rows = _base_stage2_rows()
    cpi_pred = np.array([0.08, 0.02], dtype=float)

    feature_matrix = build_cpi_to_share_feature_matrix(rows, cpi_pred)

    assert np.allclose(feature_matrix["population_log"], 0.0)
    assert np.allclose(feature_matrix["penetration_raw"], 0.0)
    assert np.allclose(feature_matrix["predicted_cpi_x_penetration"], 0.0)
    assert np.allclose(feature_matrix["market_size_log"], np.log1p(rows["market_size"]))
