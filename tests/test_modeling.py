from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent
OBOS_DIR = ROOT_DIR / "obos"
if str(OBOS_DIR) not in sys.path:
    sys.path.insert(0, str(OBOS_DIR))

from fit_weighted_theoretical_cpi_model import base_features, build_context, build_tree_feature_matrix

from exschool_game.engine import ExschoolSimulator
from exschool_game.modeling import (
    apply_home_city_to_frame,
    augment_model_matrix_with_home_city,
    build_cpi_to_share_feature_matrix,
    fit_share_model_from_cpi,
    infer_team_home_cities,
    predict_share_from_cpi_model,
    WeightedBlendRegressor,
    weighted_r2_score,
)
from scripts.model.evaluation.evaluate_current_market_pipeline import parse_obos_summary


class _StubRegressor:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        return np.full(len(X), self.value, dtype=float)


def test_infer_team_home_cities_picks_highest_scored_market() -> None:
    df = pd.DataFrame(
        [
            {"team": "13", "market": "Shanghai", "agents": 2, "marketing_investment": 1000.0, "market_share": 0.2, "sales_volume": 500.0, "market_size": 1000.0},
            {"team": "13", "market": "Chengdu", "agents": 1, "marketing_investment": 10.0, "market_share": 0.01, "sales_volume": 10.0, "market_size": 1000.0},
        ]
    )
    homes = infer_team_home_cities(df)
    assert homes["13"] == "Shanghai"


def test_apply_and_augment_home_city_features() -> None:
    df = pd.DataFrame(
        [
            {"team": "13", "market": "Shanghai", "prev_marketshare_clean": 0.1},
            {"team": "7", "market": "Chengdu", "prev_marketshare_clean": 0.2},
        ]
    )
    with_home = apply_home_city_to_frame(df, {"7": "Chengdu"}, team_id="13", current_home_city="Shanghai")
    X = pd.DataFrame({"price_rank_pct": [0.6, 0.2], "m_rank_pct": [0.7, 0.1], "q_rank_pct": [0.5, 0.3], "mi_rank_pct": [0.9, 0.2]})
    augmented = augment_model_matrix_with_home_city(X, with_home)
    assert list(augmented["is_home_market"]) == [1.0, 1.0]
    assert float(augmented.loc[0, "home_x_price_rank"]) == 0.6


def test_weighted_r2_score_matches_perfect_and_imperfect_cases() -> None:
    actual = np.array([1.0, 2.0, 3.0])
    perfect = np.array([1.0, 2.0, 3.0])
    imperfect = np.array([1.0, 2.0, 4.0])
    weight = np.array([1.0, 1.0, 1.0])
    assert weighted_r2_score(actual, perfect, weight) == 1.0
    assert weighted_r2_score(actual, imperfect, weight) < 1.0


def test_weighted_blend_regressor_normalizes_weights() -> None:
    X = pd.DataFrame({"feature": [1.0, 2.0, 3.0]})
    blended = WeightedBlendRegressor(
        [
            (3.0, _StubRegressor(2.0)),
            (1.0, _StubRegressor(10.0)),
        ]
    )

    pred = blended.predict(X)

    assert np.allclose(pred, np.full(len(X), 4.0))


def test_share_model_from_cpi_can_learn_negative_delta() -> None:
    train = pd.DataFrame(
        [
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "13",
                "market": "Shanghai",
                "market_size": 100.0,
                "sales_volume": 30.0,
                "marketshare_clean": 0.30,
                "market_utilization_clean": 0.70,
                "prev_market_utilization_clean": 0.60,
                "m_rank_pct": 0.90,
                "q_rank_pct": 0.85,
                "mi_rank_pct": 0.80,
                "price_rank_pct": 0.10,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "7",
                "market": "Chengdu",
                "market_size": 100.0,
                "sales_volume": 25.0,
                "marketshare_clean": 0.25,
                "market_utilization_clean": 0.65,
                "prev_market_utilization_clean": 0.55,
                "m_rank_pct": 0.60,
                "q_rank_pct": 0.50,
                "mi_rank_pct": 0.45,
                "price_rank_pct": 0.35,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "8",
                "market": "Wuhan",
                "market_size": 100.0,
                "sales_volume": 40.0,
                "marketshare_clean": 0.40,
                "market_utilization_clean": 0.75,
                "prev_market_utilization_clean": 0.68,
                "m_rank_pct": 0.70,
                "q_rank_pct": 0.75,
                "mi_rank_pct": 0.65,
                "price_rank_pct": 0.25,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "9",
                "market": "Hangzhou",
                "market_size": 100.0,
                "sales_volume": 12.0,
                "marketshare_clean": 0.12,
                "market_utilization_clean": 0.50,
                "prev_market_utilization_clean": 0.45,
                "m_rank_pct": 0.20,
                "q_rank_pct": 0.30,
                "mi_rank_pct": 0.25,
                "price_rank_pct": 0.80,
            },
        ]
    )
    cpi_pred = np.array([0.80, 0.20, 0.70, 0.10], dtype=float)

    share_model = fit_share_model_from_cpi(train, cpi_pred, team_id="13")
    share_X = build_cpi_to_share_feature_matrix(train, cpi_pred)
    share_pred, share_delta = predict_share_from_cpi_model(share_model, share_X, cpi_pred)

    assert share_model["mode"] == "delta_over_cpi"
    assert share_model["train_weighted_r2"] > 0.9
    assert share_pred[0] < cpi_pred[0]
    assert share_delta[0] < 0.0


def test_build_cpi_to_share_feature_matrix_filters_fixed_products_to_selected_team() -> None:
    train = pd.DataFrame(
        [
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "13",
                "market": "Shanghai",
                "market_size": 100.0,
                "sales_volume": 10.0,
                "market_utilization_clean": 0.50,
                "prev_market_utilization_clean": 0.50,
                "m_rank_pct": 0.50,
                "q_rank_pct": 0.50,
                "mi_rank_pct": 0.50,
                "price_rank_pct": 0.50,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "7",
                "market": "Chengdu",
                "market_size": 100.0,
                "sales_volume": 10.0,
                "market_utilization_clean": 0.50,
                "prev_market_utilization_clean": 0.50,
                "m_rank_pct": 0.50,
                "q_rank_pct": 0.50,
                "mi_rank_pct": 0.50,
                "price_rank_pct": 0.50,
            },
        ]
    )
    cpi_pred = np.array([0.20, 0.20], dtype=float)
    fixed_products = {
        ("EXSCHOOL", "r1", "13"): 30.0,
        ("EXSCHOOL", "r1", "7"): 80.0,
    }

    filtered = build_cpi_to_share_feature_matrix(
        train,
        cpi_pred,
        fixed_products_by_round_team=fixed_products,
        fixed_products_team_filter={"13"},
    )
    unfiltered = build_cpi_to_share_feature_matrix(
        train,
        cpi_pred,
        fixed_products_by_round_team=fixed_products,
    )

    assert np.allclose(filtered["stock_to_demand_ratio"], [1.5, 0.5])
    assert np.allclose(unfiltered["stock_to_demand_ratio"], [1.5, 4.0])


def test_fit_share_model_from_cpi_ignores_opponent_fixed_products_during_training() -> None:
    train = pd.DataFrame(
        [
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "13",
                "market": "Shanghai",
                "market_size": 100.0,
                "sales_volume": 30.0,
                "marketshare_clean": 0.30,
                "market_utilization_clean": 0.70,
                "prev_market_utilization_clean": 0.60,
                "m_rank_pct": 0.90,
                "q_rank_pct": 0.85,
                "mi_rank_pct": 0.80,
                "price_rank_pct": 0.10,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "7",
                "market": "Chengdu",
                "market_size": 100.0,
                "sales_volume": 25.0,
                "marketshare_clean": 0.25,
                "market_utilization_clean": 0.65,
                "prev_market_utilization_clean": 0.55,
                "m_rank_pct": 0.60,
                "q_rank_pct": 0.50,
                "mi_rank_pct": 0.45,
                "price_rank_pct": 0.35,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "8",
                "market": "Wuhan",
                "market_size": 100.0,
                "sales_volume": 40.0,
                "marketshare_clean": 0.40,
                "market_utilization_clean": 0.75,
                "prev_market_utilization_clean": 0.68,
                "m_rank_pct": 0.70,
                "q_rank_pct": 0.75,
                "mi_rank_pct": 0.65,
                "price_rank_pct": 0.25,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r1",
                "team": "9",
                "market": "Hangzhou",
                "market_size": 100.0,
                "sales_volume": 12.0,
                "marketshare_clean": 0.12,
                "market_utilization_clean": 0.50,
                "prev_market_utilization_clean": 0.45,
                "m_rank_pct": 0.20,
                "q_rank_pct": 0.30,
                "mi_rank_pct": 0.25,
                "price_rank_pct": 0.80,
            },
        ]
    )
    cpi_pred = np.array([0.80, 0.20, 0.70, 0.10], dtype=float)

    team13_only = {("EXSCHOOL", "r1", "13"): 30.0}
    with_opponents = {
        ("EXSCHOOL", "r1", "13"): 30.0,
        ("EXSCHOOL", "r1", "7"): 250.0,
        ("EXSCHOOL", "r1", "8"): 400.0,
        ("EXSCHOOL", "r1", "9"): 120.0,
    }

    model_team13_only = fit_share_model_from_cpi(
        train,
        cpi_pred,
        team_id="13",
        fixed_products_by_round_team=team13_only,
    )
    model_with_opponents = fit_share_model_from_cpi(
        train,
        cpi_pred,
        team_id="13",
        fixed_products_by_round_team=with_opponents,
    )

    assert model_team13_only["name"] == model_with_opponents["name"]
    assert np.isclose(model_team13_only["train_weighted_r2"], model_with_opponents["train_weighted_r2"])
    assert np.allclose(
        model_team13_only["train_predicted_marketshare"],
        model_with_opponents["train_predicted_marketshare"],
    )


def test_engine_runtime_share_features_ignore_opponent_fixed_products() -> None:
    simulator = ExschoolSimulator.__new__(ExschoolSimulator)
    simulator.fixed_products_by_round_team = {
        ("EXSCHOOL", "r5", "13"): 30.0,
        ("EXSCHOOL", "r5", "7"): 80.0,
    }
    runtime_rows = pd.DataFrame(
        [
            {
                "competition": "EXSCHOOL",
                "round": "r5",
                "team": "13",
                "market": "Shanghai",
                "market_size": 100.0,
                "sales_volume": 0.0,
                "market_utilization_clean": 0.50,
                "prev_market_utilization_clean": 0.50,
                "m_rank_pct": 0.50,
                "q_rank_pct": 0.50,
                "mi_rank_pct": 0.50,
                "price_rank_pct": 0.50,
            },
            {
                "competition": "EXSCHOOL",
                "round": "r5",
                "team": "7",
                "market": "Chengdu",
                "market_size": 100.0,
                "sales_volume": 0.0,
                "market_utilization_clean": 0.50,
                "prev_market_utilization_clean": 0.50,
                "m_rank_pct": 0.50,
                "q_rank_pct": 0.50,
                "mi_rank_pct": 0.50,
                "price_rank_pct": 0.50,
            },
        ]
    )
    cpi_pred = np.array([0.20, 0.20], dtype=float)

    share_X = simulator._build_cpi_to_share_feature_matrix(runtime_rows, cpi_pred)

    assert np.allclose(share_X["stock_to_demand_ratio"], [1.5, 0.0])


def test_tree_feature_matrix_includes_market_context_features() -> None:
    df = pd.DataFrame(
        [
            {
                "round": "r1",
                "market": "Shanghai",
                "team": "13",
                "management_index": 100.0,
                "quality_index": 20.0,
                "market_index": 3000.0,
                "prev_team_management_index": 50.0,
                "prev_team_quality_index": 10.0,
                "avg_price_clean": 20000.0,
                "price": 19000.0,
                "prev_marketshare_clean": 0.10,
                "prev_marketshare_reported": 0.08,
                "market_utilization_clean": 0.5,
                "population": 6_000_000.0,
                "penetration": 0.02,
                "agents": 3.0,
                "marketing_investment": 500_000.0,
                "market_size": 120_000.0,
            },
            {
                "round": "r1",
                "market": "Wuhan",
                "team": "7",
                "management_index": 80.0,
                "quality_index": 18.0,
                "market_index": 2500.0,
                "prev_team_management_index": 40.0,
                "prev_team_quality_index": 9.0,
                "avg_price_clean": 21000.0,
                "price": 20500.0,
                "prev_marketshare_clean": 0.05,
                "prev_marketshare_reported": 0.04,
                "market_utilization_clean": 0.4,
                "population": 2_500_000.0,
                "penetration": 0.013,
                "agents": 2.0,
                "marketing_investment": 200_000.0,
                "market_size": 32_500.0,
            },
        ]
    )

    feats = base_features(df)
    context = build_context(feats, ["r1"], ["Shanghai", "Wuhan"])
    X = build_tree_feature_matrix(feats, context)

    for column in [
        "population_log",
        "penetration_raw",
        "penetration_logit",
        "population_x_penetration",
        "penetration_x_m_rank",
        "penetration_x_q_rank",
        "penetration_x_mi_rank",
        "market_size_log",
        "market_size_x_price_rank",
    ]:
        assert column in X.columns
        assert np.isfinite(X[column]).all()


def test_parse_obos_summary_keeps_population_penetration_and_manual_cpi() -> None:
    with TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "r1_summary.xlsx"
        sheet = pd.DataFrame(
            [
                ["Market Report - Hangzhou", None, None, None, None, None, None, None, None],
                [None, None, None, None, None, None, None, None, None],
                [None, None, None, None, None, None, None, None, None],
                ["2,500,000", "1.30%", "32,500", "5,500", "24,000", None, None, None, None],
                [None, None, None, None, None, None, None, None, None],
                ["Team", "Management", "Agents", "Marketing", "Quality", "Price", "Sales", "Market Share", "竞争力"],
                ["9", "1105.07", "1", "3,333", "1.01", "24,443", "622", "1.91%", None],
                [None, None, None, None, None, None, None, None, None],
            ]
        )
        with pd.ExcelWriter(path) as writer:
            sheet.to_excel(writer, sheet_name="Market Report", header=False, index=False)

        rows = parse_obos_summary(path)

    assert len(rows) == 1
    row = rows[0]
    assert row["round"] == "r1"
    assert row["market"] == "Hangzhou"
    assert row["team"] == "9"
    assert row["population"] == 2_500_000.0
    assert np.isclose(row["penetration"], 0.013)
    assert row["market_size"] == 32_500.0
    assert np.isclose(row["market_share"], 0.0191)
    assert np.isclose(row["actual_real_cpi"], 0.0132)
