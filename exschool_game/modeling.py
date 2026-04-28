from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.tree import DecisionTreeRegressor
from .market_allocation import integer_allocate_by_weights


class WeightedBlendRegressor:
    def __init__(self, estimators: list[tuple[float, Any]]):
        self.estimators = [(float(weight), estimator) for weight, estimator in estimators if float(weight) > 0 and estimator is not None]
        if not self.estimators:
            raise ValueError("WeightedBlendRegressor requires at least one positive-weight estimator.")
        total_weight = sum(weight for weight, _ in self.estimators)
        self.normalized_estimators = [(weight / total_weight, estimator) for weight, estimator in self.estimators]

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        blended: np.ndarray | None = None
        for weight, estimator in self.normalized_estimators:
            pred = np.asarray(estimator.predict(X), dtype=float)
            blended = pred * weight if blended is None else blended + pred * weight
        if blended is None:
            raise RuntimeError("WeightedBlendRegressor produced no prediction.")
        return blended


def infer_team_home_cities(market_df: pd.DataFrame) -> dict[str, str]:
    if market_df.empty:
        return {}
    scored = market_df.copy()
    scored["home_city_score"] = (
        scored["agents"].fillna(0.0) * 3.0
        + np.log1p(scored["marketing_investment"].fillna(0.0).clip(lower=0.0))
        + scored["market_share"].fillna(0.0) * 100.0
        + scored["sales_volume"].fillna(0.0) / np.maximum(scored["market_size"].fillna(1.0), 1.0) * 50.0
    )
    summary = (
        scored.groupby(["team", "market"], as_index=False)["home_city_score"]
        .sum()
        .sort_values(["team", "home_city_score", "market"], ascending=[True, False, True])
    )
    best = summary.groupby("team", as_index=False).first()
    return {str(row["team"]): str(row["market"]) for _, row in best.iterrows()}


def apply_home_city_to_frame(
    df: pd.DataFrame,
    team_home_city_map: dict[str, str],
    *,
    team_id: str,
    current_home_city: str | None = None,
    home_city_overrides: dict[str, str] | None = None,
) -> pd.DataFrame:
    out = df.copy()
    out["home_city"] = out["team"].astype(str).map(team_home_city_map)
    if home_city_overrides:
        for override_team_id, override_city in home_city_overrides.items():
            if not str(override_city or "").strip():
                continue
            out.loc[out["team"].astype(str) == str(override_team_id), "home_city"] = str(override_city)
    if current_home_city:
        out.loc[out["team"].astype(str) == team_id, "home_city"] = str(current_home_city)
    out["home_city"] = out["home_city"].fillna(out["market"])
    return out


def augment_model_matrix_with_home_city(X: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    out = X.copy()
    home_city_series = df["home_city"] if "home_city" in df.columns else df["market"]
    is_home_market = (df["market"].astype(str) == home_city_series.astype(str)).astype(float)
    brand_log = np.log1p(df["prev_marketshare_clean"].fillna(0.0).clip(lower=0.0) * 1000.0)

    def first_col(name: str) -> pd.Series:
        if name not in out.columns:
            return pd.Series(0.0, index=out.index, dtype=float)
        value = out.loc[:, name]
        if isinstance(value, pd.DataFrame):
            return value.iloc[:, 0].astype(float)
        return value.astype(float)

    out["is_home_market"] = is_home_market
    out["home_x_price_rank"] = is_home_market * first_col("price_rank_pct")
    out["home_x_m_rank"] = is_home_market * first_col("m_rank_pct")
    out["home_x_q_rank"] = is_home_market * first_col("q_rank_pct")
    out["home_x_mi_rank"] = is_home_market * first_col("mi_rank_pct")
    out["home_x_brand"] = is_home_market * brand_log
    return out


def weighted_r2_score(actual: np.ndarray, pred: np.ndarray, sample_weight: np.ndarray) -> float:
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    weight = np.asarray(sample_weight, dtype=float)
    mask = np.isfinite(actual) & np.isfinite(pred) & np.isfinite(weight) & (weight > 0)
    if not np.any(mask):
        return float("nan")
    actual = actual[mask]
    pred = pred[mask]
    weight = weight[mask]
    weight_sum = float(weight.sum())
    if weight_sum <= 0:
        return float("nan")
    actual_mean = float(np.average(actual, weights=weight))
    ss_res = float(np.sum(weight * np.square(actual - pred)))
    ss_tot = float(np.sum(weight * np.square(actual - actual_mean)))
    if ss_tot <= 0:
        return float("nan")
    return 1.0 - ss_res / ss_tot


def build_cpi_to_share_feature_matrix(
    df: pd.DataFrame,
    predicted_cpi: np.ndarray,
    *,
    fixed_products_by_round_team: dict[tuple[str, str, str], float] | None = None,
    fixed_products_team_filter: set[str] | None = None,
) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    pred_cpi = np.maximum(np.asarray(predicted_cpi, dtype=float), 0.0)
    market_size = df["market_size"].fillna(0.0).clip(lower=0.0)
    predicted_demand_units = pred_cpi * market_size.to_numpy(dtype=float)

    competition_series = (
        df["competition"].astype(str)
        if "competition" in df.columns
        else pd.Series("EXSCHOOL", index=df.index, dtype="object")
    )
    total_products_lookup: dict[tuple[str, str, str], float] = {}
    if fixed_products_by_round_team:
        if fixed_products_team_filter is None:
            total_products_lookup.update(fixed_products_by_round_team)
        else:
            allowed_teams = {str(team).strip() for team in fixed_products_team_filter if str(team).strip()}
            for lookup_key, total_products in fixed_products_by_round_team.items():
                competition, round_id, team = lookup_key
                if str(team).strip() in allowed_teams:
                    total_products_lookup[(str(competition), str(round_id), str(team))] = float(total_products)
    fallback_totals = (
        df.assign(competition=competition_series)
        .groupby(["competition", "round", "team"], as_index=False)["sales_volume"]
        .sum()
        .itertuples(index=False)
    )
    for row in fallback_totals:
        lookup_key = (str(row.competition), str(row.round), str(row.team))
        total_products_lookup[lookup_key] = max(
            float(total_products_lookup.get(lookup_key, 0.0) or 0.0),
            float(row.sales_volume),
        )

    team_total_products = np.array([
        max(float(total_products_lookup.get((str(competition), str(round_id), str(team)), 0.0) or 0.0), 0.0)
        for competition, round_id, team in zip(competition_series, df["round"], df["team"], strict=False)
    ], dtype=float)
    stock_units_est_series = pd.Series(0.0, index=df.index, dtype=float)
    grouped_keys = df["round"].astype(str) + "||" + df["team"].astype(str)
    for _, row_idx in grouped_keys.groupby(grouped_keys).groups.items():
        indices = list(row_idx)
        total_products = int(max(round(float(team_total_products[indices[0]])), 0))
        weights = predicted_demand_units[indices]
        stock_units_est_series.loc[indices] = integer_allocate_by_weights(total_products, weights)

    stock_units_est = stock_units_est_series.to_numpy(dtype=float)
    out["predicted_cpi"] = pred_cpi
    prev_util = df["prev_market_utilization_clean"].fillna(df["market_utilization_clean"]).fillna(0.0).clip(lower=0.0, upper=1.0)
    out["market_slack"] = (1.0 - prev_util).clip(lower=0.0, upper=1.0)
    out["stock_to_demand_ratio"] = np.clip(
        stock_units_est / np.maximum(predicted_demand_units, 1.0),
        0.0,
        5.0,
    )
    m_rank_pct = df["m_rank_pct"].fillna(0.0)
    q_rank_pct = df["q_rank_pct"].fillna(0.0)
    mi_rank_pct = df["mi_rank_pct"].fillna(0.0)
    price_rank_pct = df["price_rank_pct"].fillna(0.0)
    out["m_gate"] = (m_rank_pct >= 0.60).astype(float)
    out["q_gate"] = (q_rank_pct >= 0.60).astype(float)
    out["mi_gate"] = (mi_rank_pct >= 0.60).astype(float)
    out["price_gate"] = (price_rank_pct >= 0.60).astype(float)
    out["gate_sum"] = out["m_gate"] + out["q_gate"] + out["mi_gate"] + out["price_gate"]
    out["predicted_cpi_x_slack"] = out["predicted_cpi"] * out["market_slack"]
    out["predicted_cpi_x_stock_to_demand_ratio"] = out["predicted_cpi"] * out["stock_to_demand_ratio"]
    out["market_slack_x_stock_to_demand_ratio"] = out["market_slack"] * out["stock_to_demand_ratio"]
    out["gate_sum_x_market_slack"] = out["gate_sum"] * out["market_slack"]
    out["gate_sum_x_stock_to_demand_ratio"] = out["gate_sum"] * out["stock_to_demand_ratio"]
    out["predicted_cpi_x_gate_sum"] = out["predicted_cpi"] * out["gate_sum"]
    return out.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def fit_share_model_from_cpi(
    train: pd.DataFrame,
    cpi_pred: np.ndarray,
    *,
    team_id: str,
    fixed_products_by_round_team: dict[tuple[str, str, str], float] | None = None,
) -> dict[str, Any]:
    X_share = build_cpi_to_share_feature_matrix(
        train,
        cpi_pred,
        fixed_products_by_round_team=fixed_products_by_round_team,
        fixed_products_team_filter={str(team_id)},
    )
    y_share = train["marketshare_clean"].fillna(0.0).clip(lower=0.0, upper=1.0).to_numpy(dtype=float)
    share_weight = np.where(train["team"].astype(str) == team_id, 100.0, 1.0)
    base_share = np.clip(np.asarray(cpi_pred, dtype=float), 0.0, 1.0)
    delta_target = np.clip(y_share - base_share, -1.0, 1.0)

    candidates: list[tuple[str, Any]] = [
        (
            "gbr_delta",
            GradientBoostingRegressor(
                loss="squared_error",
                n_estimators=400,
                learning_rate=0.04,
                max_depth=3,
                min_samples_leaf=2,
                subsample=0.85,
                random_state=42,
            ),
        ),
        (
            "gbr_deep",
            GradientBoostingRegressor(
                loss="squared_error",
                n_estimators=800,
                learning_rate=0.03,
                max_depth=4,
                min_samples_leaf=1,
                subsample=0.9,
                random_state=42,
            ),
        ),
        (
            "rf_dense",
            RandomForestRegressor(
                n_estimators=800,
                max_depth=None,
                min_samples_leaf=1,
                random_state=42,
                n_jobs=-1,
            ),
        ),
        (
            "tree_memorizer",
            DecisionTreeRegressor(
                max_depth=None,
                min_samples_leaf=1,
                random_state=42,
            ),
        ),
    ]
    if len(X_share) == 0:
        return {
            "estimator": None,
            "columns": X_share.columns.tolist(),
            "name": "identity_cpi_share",
            "train_weighted_r2": float(weighted_r2_score(y_share, base_share, share_weight)),
            "train_predicted_marketshare": base_share,
            "mode": "identity",
            "delta_samples": 0,
            "uplift_samples": 0,
        }

    best_name = ""
    best_model: Any = None
    best_pred: np.ndarray | None = None
    best_r2 = -np.inf

    for name, model in candidates:
        model_instance = clone(model)
        model_instance.fit(X_share, delta_target, sample_weight=share_weight)
        delta_pred = np.clip(model_instance.predict(X_share), -1.0, 1.0)
        pred = np.clip(base_share + delta_pred, 0.0, 1.0)
        r2 = weighted_r2_score(y_share, pred, share_weight)
        if np.isfinite(r2) and r2 > best_r2:
            best_name = name
            best_model = model_instance
            best_pred = pred
            best_r2 = r2
        if np.isfinite(r2) and r2 >= 0.99:
            best_name = name
            best_model = model_instance
            best_pred = pred
            best_r2 = r2
            break

    if best_model is None or best_pred is None:
        raise RuntimeError("Failed to fit CPI-to-share model")

    return {
        "estimator": best_model,
        "columns": X_share.columns.tolist(),
        "name": best_name,
        "train_weighted_r2": float(best_r2),
        "train_predicted_marketshare": best_pred,
        "mode": "delta_over_cpi",
        "delta_samples": int(len(X_share)),
        "uplift_samples": int(len(X_share)),
    }


def predict_share_from_cpi_model(
    share_model: dict[str, Any],
    share_X: pd.DataFrame,
    cpi_pred: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    base_share = np.clip(np.asarray(cpi_pred, dtype=float), 0.0, 1.0)
    estimator = share_model.get("estimator")
    if share_model.get("mode") == "identity" or estimator is None:
        return base_share, np.zeros(len(base_share), dtype=float)

    raw_pred = estimator.predict(share_X.reindex(columns=share_model["columns"], fill_value=0.0))
    if share_model.get("mode") == "uplift_over_cpi":
        uplift_pred = np.clip(raw_pred, 0.0, 1.0)
        supply_ok = share_X["stock_to_demand_ratio"].to_numpy(dtype=float) >= 1.0
        share_pred = np.clip(base_share + uplift_pred * supply_ok.astype(float), 0.0, 1.0)
        return share_pred, uplift_pred

    delta_pred = np.clip(raw_pred, -1.0, 1.0)
    share_pred = np.clip(base_share + delta_pred, 0.0, 1.0)
    return share_pred, delta_pred
