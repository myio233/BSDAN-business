#!/usr/bin/env python3
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.tree import DecisionTreeRegressor, export_text

from analyze_team24_competitiveness import round_sort_key
from analyze_team24_competitiveness import build_sample_table, load_market_reports, load_summary_samples
from fit_team24_semidynamic_model import attach_lagged_features


BASE_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results")
OUTPUT_XLSX = BASE_DIR / "r1_r6_peer_global_constrained_cpi.xlsx"
MODEL_XLSX = BASE_DIR / "global_constrained_cpi_model.xlsx"
OUTPUT_MD = BASE_DIR / "global_constrained_cpi_model.md"
TEAM_ID = "24"
EPS = 1e-9
STAGE1_RESIDUAL_CALIBRATION_MARKET = "Hangzhou"
STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD = 0.04
STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE = 1.2
STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT = 0.75
STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD = 0.5
STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD = 0.12
STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_EXTRA_LOG_SLOPE = 0.8
STAGE1_RESIDUAL_CALIBRATION_LATE_CHALLENGER_SHIFT_MULTIPLIER = 0.3
STAGE1_RESIDUAL_CALIBRATION_LATE_MAX_LOG_SHIFT = 1.5
STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD = 4
STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT = 0.82


def writable_path(path):
    candidates = [path]
    candidates.extend(path.with_name(f"{path.stem}_updated{idx}{path.suffix}") for idx in range(1, 10))
    for candidate in candidates:
        try:
            with open(candidate, "ab"):
                pass
            return candidate
        except OSError:
            continue
    return path.with_name(f"{path.stem}_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")


def quantile_grid(series, probs):
    arr = pd.Series(series).replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
    if len(arr) == 0:
        return [0.0]
    vals = sorted({float(np.quantile(arr, p)) for p in probs})
    return vals or [float(np.median(arr))]


def threshold_pairs(series, low_probs, high_probs):
    lows = quantile_grid(series, low_probs)
    highs = quantile_grid(series, high_probs)
    pairs = []
    for low in lows:
        for high in highs:
            if high > low + 1e-9:
                pairs.append((low, high))
    return pairs or [(lows[0], highs[-1] if highs[-1] > lows[0] else lows[0] + 1e-6)]


def relu(values, threshold):
    return np.maximum(np.asarray(values, dtype=float) - threshold, 0.0)


def step(values, threshold):
    return (np.asarray(values, dtype=float) >= threshold).astype(float)


def metrics(actual, pred):
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    mask = np.isfinite(actual) & np.isfinite(pred)
    if not np.any(mask):
        return {"mae": np.nan, "rmse": np.nan, "r2": np.nan, "corr": np.nan}
    actual = actual[mask]
    pred = pred[mask]
    err = pred - actual
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err ** 2)))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    r2 = float(1 - np.sum(err ** 2) / ss_tot) if ss_tot > 0 else np.nan
    corr = float(np.corrcoef(actual, pred)[0, 1]) if len(actual) > 1 else np.nan
    return {"mae": mae, "rmse": rmse, "r2": r2, "corr": corr}


def compute_stage1_residual_log_shift(
    pred_log,
    markets,
    *,
    rounds=None,
    prev_marketshare_clean=None,
    prev_market_utilization_clean=None,
    market=STAGE1_RESIDUAL_CALIBRATION_MARKET,
    cpi_threshold=STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD,
    log_slope=STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE,
    max_log_shift=STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT,
    late_utilization_threshold=STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD,
    late_incumbent_share_threshold=STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD,
    late_incumbent_extra_log_slope=STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_EXTRA_LOG_SLOPE,
    late_challenger_shift_multiplier=STAGE1_RESIDUAL_CALIBRATION_LATE_CHALLENGER_SHIFT_MULTIPLIER,
    late_max_log_shift=STAGE1_RESIDUAL_CALIBRATION_LATE_MAX_LOG_SHIFT,
    round_aware_late_round_threshold=STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD,
    round_aware_late_max_log_shift=STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT,
):
    pred_log_arr = np.asarray(pred_log, dtype=float)
    market_arr = pd.Series(markets, copy=False).fillna("").astype(str).to_numpy()
    if rounds is None:
        round_order_arr = np.zeros(len(pred_log_arr), dtype=int)
    else:
        round_order_arr = (
            pd.Series(rounds, copy=False)
            .map(round_sort_key)
            .fillna(0)
            .to_numpy(dtype=int)
        )
    active_market = market_arr == str(market)
    high_cpi_log = np.maximum(pred_log_arr - np.log(max(float(cpi_threshold), EPS)), 0.0)
    shift = np.minimum(float(log_slope) * high_cpi_log, max(float(max_log_shift), 0.0))

    if prev_marketshare_clean is None:
        prev_share_arr = np.zeros(len(pred_log_arr), dtype=float)
    else:
        prev_share_arr = pd.to_numeric(pd.Series(prev_marketshare_clean, copy=False), errors="coerce").fillna(0.0).to_numpy(dtype=float)
    if prev_market_utilization_clean is None:
        prev_util_arr = np.zeros(len(pred_log_arr), dtype=float)
    else:
        prev_util_arr = pd.to_numeric(pd.Series(prev_market_utilization_clean, copy=False), errors="coerce").fillna(0.0).to_numpy(dtype=float)

    late_regime = active_market & (high_cpi_log > 0.0) & (prev_util_arr >= float(late_utilization_threshold))
    late_incumbent = late_regime & (prev_share_arr >= float(late_incumbent_share_threshold))
    late_challenger = late_regime & ~late_incumbent
    late_incumbent_cap = np.where(
        round_order_arr >= int(round_aware_late_round_threshold),
        float(round_aware_late_max_log_shift),
        float(late_max_log_shift),
    )

    shift = np.where(
        late_incumbent,
        np.minimum(
            shift + float(late_incumbent_extra_log_slope) * high_cpi_log,
            np.maximum(late_incumbent_cap, 0.0),
        ),
        shift,
    )
    shift = np.where(
        late_challenger,
        shift * np.clip(float(late_challenger_shift_multiplier), 0.0, 1.0),
        shift,
    )
    return np.where(active_market, shift, 0.0)


def apply_stage1_residual_calibration(pred_log, markets, **kwargs):
    pred_log_arr = np.asarray(pred_log, dtype=float)
    shift = compute_stage1_residual_log_shift(pred_log_arr, markets, **kwargs)
    adjusted_log = pred_log_arr + shift
    return adjusted_log, shift


def safe_r2(metric_dict):
    value = metric_dict["r2"]
    return value if np.isfinite(value) else -1.0


def clean_market_table(df):
    out = df.copy()
    out["round_order"] = out["round"].map(round_sort_key)

    price_fix_mask = out["price"].fillna(0).between(0.01, 999.999) & (out["avg_price"].fillna(0) > 5000)
    out.loc[price_fix_mask, "price"] = out.loc[price_fix_mask, "price"] * 1000.0

    median_price = out.groupby(["round", "market"])["price"].transform("median")
    bad_avg = (
        out["avg_price"].isna()
        | (out["avg_price"] <= 0)
        | (median_price > 0)
        & ((out["avg_price"] < 0.5 * median_price) | (out["avg_price"] > 2.0 * median_price))
    )
    out["avg_price_clean"] = out["avg_price"].where(~bad_avg, median_price)

    out["marketshare_reported"] = out["market_share"].clip(lower=0).fillna(0.0)
    out["marketshare_clean"] = (
        out["sales_volume"].fillna(0).clip(lower=0)
        / out["market_size"].replace(0, np.nan)
    ).clip(lower=0, upper=1.0).fillna(0.0)
    out["market_utilization_clean"] = (
        out["total_sales_volume"].fillna(0).clip(lower=0)
        / out["market_size"].replace(0, np.nan)
    ).clip(lower=0, upper=1.0).fillna(0.0)

    out = out.sort_values(["team", "market", "round_order"]).reset_index(drop=True)
    market_state = (
        out[["market", "round", "round_order", "market_utilization_clean"]]
        .drop_duplicates(subset=["market", "round"])
        .sort_values(["market", "round_order"])
        .reset_index(drop=True)
    )
    market_state["prev_market_utilization_clean"] = market_state.groupby("market")["market_utilization_clean"].shift(1)
    out = out.merge(
        market_state[["market", "round", "prev_market_utilization_clean"]],
        on=["market", "round"],
        how="left",
    )
    out["prev_marketshare_clean"] = out.groupby(["team", "market"])["marketshare_clean"].shift(1)
    out["prev_marketshare_reported"] = out.groupby(["team", "market"])["marketshare_reported"].shift(1)
    return out


def base_features(df):
    out = df.copy()

    m_raw = out["management_index"].fillna(0).clip(lower=0)
    q_raw = out["quality_index"].fillna(0).clip(lower=0)
    mi_raw = out["market_index"].fillna(0).clip(lower=0)
    m_prev_raw = out["prev_team_management_index"].fillna(0).clip(lower=0)
    q_prev_raw = out["prev_team_quality_index"].fillna(0).clip(lower=0)

    out["m_raw"] = m_raw
    out["q_raw"] = q_raw
    out["mi_raw"] = mi_raw
    out["m_prev_raw"] = m_prev_raw
    out["q_prev_raw"] = q_prev_raw
    out["market_utilization_clean"] = out["market_utilization_clean"].fillna(0.0)

    out["m_log"] = np.log1p(m_raw)
    out["q_log"] = np.log1p(q_raw)
    out["mi_log"] = np.log1p(mi_raw)
    out["m_prev_log"] = np.log1p(m_prev_raw)
    out["q_prev_log"] = np.log1p(q_prev_raw)

    out["m_k"] = m_raw / 1000.0
    out["q_k"] = q_raw / 1000.0
    out["mi_m"] = mi_raw / 1_000_000.0
    out["m_prev_k"] = m_prev_raw / 1000.0
    out["q_prev_k"] = q_prev_raw / 1000.0

    avg_price_clean = out["avg_price_clean"].fillna(out["price"]).replace(0, np.nan)
    price = out["price"].replace(0, np.nan)
    price_ratio = (avg_price_clean / price).replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(0.5, 2.0)
    out["p_log"] = np.log(price_ratio)
    out["price_discount"] = ((avg_price_clean - out["price"]) / avg_price_clean).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(-0.5, 0.5)

    out["brand_log"] = np.log1p(out["prev_marketshare_clean"].fillna(0).clip(lower=0) * 1000.0)
    out["reported_brand_log"] = np.log1p(out["prev_marketshare_reported"].fillna(0).clip(lower=0) * 1000.0)
    out["team24_home_shanghai"] = ((out["team"] == TEAM_ID) & (out["market"] == "Shanghai")).astype(float)
    out["is_shanghai"] = (out["market"] == "Shanghai").astype(float)
    population_raw = out.get("population", pd.Series(0.0, index=out.index, dtype=float)).fillna(0.0).clip(lower=0.0)
    penetration_raw = out.get("penetration", pd.Series(0.0, index=out.index, dtype=float)).fillna(0.0).clip(lower=0.0)
    penetration_clipped = np.clip(penetration_raw, 1e-6, 1.0 - 1e-6)
    out["population_log"] = np.log1p(population_raw)
    out["penetration_raw"] = penetration_raw
    out["penetration_logit"] = np.log(penetration_clipped / (1.0 - penetration_clipped))
    out["population_x_penetration"] = out["population_log"] * penetration_raw

    group_cols = ["round", "market"]
    out["num_teams_market"] = out.groupby(group_cols)["team"].transform("size").astype(float)

    def rel_stats(col, prefix, higher_is_better=True, positive=True):
        grp = out.groupby(group_cols)[col]
        median = grp.transform("median").fillna(0.0)
        mean = grp.transform("mean").fillna(0.0)
        total = grp.transform("sum").fillna(0.0)
        maxv = grp.transform("max").fillna(0.0)
        val = out[col].fillna(0.0)

        out[f"{prefix}_market_median"] = median
        out[f"{prefix}_market_mean"] = mean
        out[f"{prefix}_market_total"] = total
        out[f"{prefix}_market_max"] = maxv
        out[f"{prefix}_share"] = (val / total.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        if positive:
            out[f"{prefix}_vs_median_log"] = np.log1p(val.clip(lower=0.0)) - np.log1p(median.clip(lower=0.0))
            out[f"{prefix}_vs_max_log"] = np.log1p(val.clip(lower=0.0)) - np.log1p(maxv.clip(lower=0.0))
        else:
            out[f"{prefix}_vs_median_log"] = np.log((median + 1.0) / (val + 1.0))
            out[f"{prefix}_vs_max_log"] = np.log((maxv + 1.0) / (val + 1.0))

        asc = not higher_is_better
        rank_pct = grp.rank(method="average", pct=True, ascending=asc).astype(float)
        if higher_is_better:
            out[f"{prefix}_rank_pct"] = rank_pct
        else:
            out[f"{prefix}_rank_pct"] = 1.0 - rank_pct + (1.0 / out["num_teams_market"].replace(0, np.nan)).fillna(0.0)

    rel_stats("price", "price", higher_is_better=False, positive=False)
    rel_stats("management_index", "m", higher_is_better=True, positive=True)
    rel_stats("quality_index", "q", higher_is_better=True, positive=True)
    rel_stats("market_index", "mi", higher_is_better=True, positive=True)
    rel_stats("marketing_investment", "marketing", higher_is_better=True, positive=True)
    rel_stats("agents", "agents", higher_is_better=True, positive=True)

    out["decision_power"] = out["m_share"] + out["q_share"] + out["mi_share"] + out["price_rank_pct"]
    out["market_gate_power"] = out["mi_share"] * out["q_rank_pct"] * out["price_rank_pct"]
    out["quality_price_synergy"] = out["q_vs_median_log"] * out["price_vs_median_log"]
    out["management_market_synergy"] = out["m_vs_median_log"] * out["mi_vs_median_log"]
    out["quality_market_synergy"] = out["q_vs_median_log"] * out["mi_vs_median_log"]
    out["penetration_x_m_rank"] = out["penetration_raw"] * out["m_rank_pct"]
    out["penetration_x_q_rank"] = out["penetration_raw"] * out["q_rank_pct"]
    out["penetration_x_mi_rank"] = out["penetration_raw"] * out["mi_rank_pct"]
    out["market_size_log"] = np.log1p(out["market_size"].fillna(0.0).clip(lower=0.0))
    out["market_size_x_price_rank"] = out["market_size_log"] * out["price_rank_pct"]
    return out


def build_context(feats, round_levels, city_levels):
    X = pd.DataFrame(index=feats.index)
    X["const"] = 1.0
    X["m_log_base"] = feats["m_log"]
    X["q_log_base"] = feats["q_log"]
    X["mi_log_base"] = feats["mi_log"]
    X["p_log_base"] = feats["p_log"]
    X["m_prev_log"] = feats["m_prev_log"]
    X["q_prev_log"] = feats["q_prev_log"]
    X["brand_log"] = feats["brand_log"]
    X["reported_brand_log"] = feats["reported_brand_log"]
    X["team24_home_shanghai"] = feats["team24_home_shanghai"]
    X["is_shanghai"] = feats["is_shanghai"]
    X["price_discount"] = feats["price_discount"]
    X["price_vs_median_log"] = feats["price_vs_median_log"]
    X["m_vs_median_log"] = feats["m_vs_median_log"]
    X["q_vs_median_log"] = feats["q_vs_median_log"]
    X["mi_vs_median_log"] = feats["mi_vs_median_log"]
    X["marketing_vs_median_log"] = feats["marketing_vs_median_log"]
    X["agents_vs_median_log"] = feats["agents_vs_median_log"]
    X["price_rank_pct"] = feats["price_rank_pct"]
    X["m_rank_pct"] = feats["m_rank_pct"]
    X["q_rank_pct"] = feats["q_rank_pct"]
    X["mi_rank_pct"] = feats["mi_rank_pct"]
    X["marketing_rank_pct"] = feats["marketing_rank_pct"]
    X["agents_rank_pct"] = feats["agents_rank_pct"]
    X["m_share"] = feats["m_share"]
    X["q_share"] = feats["q_share"]
    X["mi_share"] = feats["mi_share"]
    X["marketing_share"] = feats["marketing_share"]
    X["agents_share"] = feats["agents_share"]
    X["decision_power"] = feats["decision_power"]
    X["market_gate_power"] = feats["market_gate_power"]
    X["quality_price_synergy"] = feats["quality_price_synergy"]
    X["management_market_synergy"] = feats["management_market_synergy"]
    X["quality_market_synergy"] = feats["quality_market_synergy"]
    X["num_teams_market"] = feats["num_teams_market"]

    round_series = pd.Series(
        pd.Categorical(feats["round"], categories=round_levels, ordered=True),
        index=feats.index,
    )
    city_series = pd.Series(
        pd.Categorical(feats["market"], categories=city_levels, ordered=True),
        index=feats.index,
    )
    round_dummies = pd.get_dummies(round_series, prefix="round", drop_first=True, dtype=float)
    city_dummies = pd.get_dummies(city_series, prefix="city", drop_first=True, dtype=float)
    return pd.concat([X, round_dummies, city_dummies], axis=1)


def build_tree_feature_matrix(feats, context):
    extra = pd.DataFrame(index=feats.index)
    extra["m_raw"] = feats["m_raw"]
    extra["q_raw"] = feats["q_raw"]
    extra["mi_raw"] = feats["mi_raw"]
    extra["m_prev_raw"] = feats["m_prev_raw"]
    extra["q_prev_raw"] = feats["q_prev_raw"]
    extra["m_k"] = feats["m_k"]
    extra["q_k"] = feats["q_k"]
    extra["mi_m"] = feats["mi_m"]
    extra["m_prev_k"] = feats["m_prev_k"]
    extra["q_prev_k"] = feats["q_prev_k"]
    extra["price"] = feats["price"].fillna(0).clip(lower=0)
    extra["avg_price_clean"] = feats["avg_price_clean"].fillna(0).clip(lower=0)
    extra["market_size"] = feats["market_size"].fillna(0).clip(lower=0)
    extra["agents"] = feats["agents"].fillna(0).clip(lower=0)
    extra["marketing_investment"] = feats["marketing_investment"].fillna(0).clip(lower=0)
    extra["price_vs_median_log"] = feats["price_vs_median_log"]
    extra["m_vs_median_log"] = feats["m_vs_median_log"]
    extra["q_vs_median_log"] = feats["q_vs_median_log"]
    extra["mi_vs_median_log"] = feats["mi_vs_median_log"]
    extra["marketing_vs_median_log"] = feats["marketing_vs_median_log"]
    extra["agents_vs_median_log"] = feats["agents_vs_median_log"]
    extra["price_rank_pct"] = feats["price_rank_pct"]
    extra["m_rank_pct"] = feats["m_rank_pct"]
    extra["q_rank_pct"] = feats["q_rank_pct"]
    extra["mi_rank_pct"] = feats["mi_rank_pct"]
    extra["marketing_rank_pct"] = feats["marketing_rank_pct"]
    extra["agents_rank_pct"] = feats["agents_rank_pct"]
    extra["m_share"] = feats["m_share"]
    extra["q_share"] = feats["q_share"]
    extra["mi_share"] = feats["mi_share"]
    extra["marketing_share"] = feats["marketing_share"]
    extra["agents_share"] = feats["agents_share"]
    extra["decision_power"] = feats["decision_power"]
    extra["market_gate_power"] = feats["market_gate_power"]
    extra["quality_price_synergy"] = feats["quality_price_synergy"]
    extra["management_market_synergy"] = feats["management_market_synergy"]
    extra["quality_market_synergy"] = feats["quality_market_synergy"]
    extra["population_log"] = feats["population_log"]
    extra["penetration_raw"] = feats["penetration_raw"]
    extra["penetration_logit"] = feats["penetration_logit"]
    extra["population_x_penetration"] = feats["population_x_penetration"]
    extra["penetration_x_m_rank"] = feats["penetration_x_m_rank"]
    extra["penetration_x_q_rank"] = feats["penetration_x_q_rank"]
    extra["penetration_x_mi_rank"] = feats["penetration_x_mi_rank"]
    extra["market_size_log"] = feats["market_size_log"]
    extra["market_size_x_price_rank"] = feats["market_size_x_price_rank"]
    extra["num_teams_market"] = feats["num_teams_market"]
    return pd.concat([context.drop(columns=["const"]), extra], axis=1).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_single_threshold_matrix(feats, context, params):
    tau_m, tau_q, tau_mi, tau_p = params
    X = pd.DataFrame(index=feats.index)
    X["m_step"] = step(feats["m_log"], tau_m)
    X["q_step"] = step(feats["q_log"], tau_q)
    X["mi_step"] = step(feats["mi_log"], tau_mi)
    X["p_step"] = step(feats["p_log"], tau_p)
    X["m_relu"] = relu(feats["m_log"], tau_m)
    X["q_relu"] = relu(feats["q_log"], tau_q)
    X["mi_relu"] = relu(feats["mi_log"], tau_mi)
    X["p_relu"] = relu(feats["p_log"], tau_p)
    X["mi_after_q"] = feats["mi_log"] * X["q_step"]
    X["mi_after_m"] = feats["mi_log"] * X["m_step"]
    X["price_after_q"] = feats["p_log"] * X["q_step"]
    X["price_after_mi"] = feats["p_log"] * X["mi_step"]
    X["m_x_q"] = X["m_relu"] * X["q_relu"]
    X["q_x_mi"] = X["q_relu"] * X["mi_relu"]
    X["m_x_mi"] = X["m_relu"] * X["mi_relu"]
    X["m_x_q_x_mi"] = X["m_relu"] * X["q_relu"] * X["mi_relu"]
    X["brand_x_mi"] = feats["brand_log"] * feats["mi_log"]
    X["qprev_x_mi"] = feats["q_prev_log"] * feats["mi_log"]
    return pd.concat([context, X], axis=1)


def build_dual_threshold_matrix(feats, context, params):
    (m1, m2), (q1, q2), (mi1, mi2), (p1, p2) = params
    X = pd.DataFrame(index=feats.index)

    X["m_low_step"] = step(feats["m_log"], m1)
    X["m_high_step"] = step(feats["m_log"], m2)
    X["q_low_step"] = step(feats["q_log"], q1)
    X["q_high_step"] = step(feats["q_log"], q2)
    X["mi_low_step"] = step(feats["mi_log"], mi1)
    X["mi_high_step"] = step(feats["mi_log"], mi2)
    X["p_low_step"] = step(feats["p_log"], p1)
    X["p_high_step"] = step(feats["p_log"], p2)

    X["m_low_relu"] = relu(feats["m_log"], m1)
    X["m_high_relu"] = relu(feats["m_log"], m2)
    X["q_low_relu"] = relu(feats["q_log"], q1)
    X["q_high_relu"] = relu(feats["q_log"], q2)
    X["mi_low_relu"] = relu(feats["mi_log"], mi1)
    X["mi_high_relu"] = relu(feats["mi_log"], mi2)
    X["p_low_relu"] = relu(feats["p_log"], p1)
    X["p_high_relu"] = relu(feats["p_log"], p2)

    X["mi_after_q_low"] = feats["mi_log"] * X["q_low_step"]
    X["mi_after_q_high"] = feats["mi_log"] * X["q_high_step"]
    X["mi_after_m_high"] = feats["mi_log"] * X["m_high_step"]
    X["price_after_q_high"] = feats["p_log"] * X["q_high_step"]
    X["price_after_mi_high"] = feats["p_log"] * X["mi_high_step"]
    X["brand_after_q_high"] = feats["brand_log"] * X["q_high_step"]
    X["m_high_x_q_high"] = X["m_high_step"] * X["q_high_step"]
    X["q_high_x_mi_high"] = X["q_high_step"] * X["mi_high_step"]
    X["m_high_x_mi_high"] = X["m_high_step"] * X["mi_high_step"]
    X["all_high_gate"] = X["m_high_step"] * X["q_high_step"] * X["mi_high_step"]
    return pd.concat([context, X], axis=1)


def build_absolute_gate_matrix(feats, context, params):
    tau_m_raw, tau_q_raw, tau_mi_raw, tau_disc = params
    X = pd.DataFrame(index=feats.index)

    X["m_abs_gate"] = step(feats["m_raw"], tau_m_raw)
    X["q_abs_gate"] = step(feats["q_raw"], tau_q_raw)
    X["mi_abs_gate"] = step(feats["mi_raw"], tau_mi_raw)
    X["price_abs_gate"] = step(feats["price_discount"], tau_disc)

    X["m_abs_relu"] = relu(feats["m_raw"], tau_m_raw) / 1000.0
    X["q_abs_relu"] = relu(feats["q_raw"], tau_q_raw) / 1000.0
    X["mi_abs_relu"] = relu(feats["mi_raw"], tau_mi_raw) / 1_000_000.0
    X["discount_relu"] = relu(feats["price_discount"], tau_disc)

    X["mi_after_q_gate"] = feats["mi_log"] * X["q_abs_gate"]
    X["mi_after_m_gate"] = feats["mi_log"] * X["m_abs_gate"]
    X["price_after_q_gate"] = feats["p_log"] * X["q_abs_gate"]
    X["price_after_mi_gate"] = feats["p_log"] * X["mi_abs_gate"]
    X["brand_after_q_gate"] = feats["brand_log"] * X["q_abs_gate"]
    X["home_after_gates"] = feats["team24_home_shanghai"] * X["q_abs_gate"] * X["mi_abs_gate"]
    X["m_q_gate"] = X["m_abs_gate"] * X["q_abs_gate"]
    X["q_mi_gate"] = X["q_abs_gate"] * X["mi_abs_gate"]
    X["m_mi_gate"] = X["m_abs_gate"] * X["mi_abs_gate"]
    X["all_gate"] = X["m_abs_gate"] * X["q_abs_gate"] * X["mi_abs_gate"]
    return pd.concat([context, X], axis=1)


def build_semidynamic_gate_matrix(feats, context, params):
    tau_m_prev, tau_q_prev, tau_brand, tau_p = params
    X = pd.DataFrame(index=feats.index)

    X["m_prev_step"] = step(feats["m_prev_log"], tau_m_prev)
    X["q_prev_step"] = step(feats["q_prev_log"], tau_q_prev)
    X["brand_step"] = step(feats["brand_log"], tau_brand)
    X["p_step"] = step(feats["p_log"], tau_p)

    X["m_prev_relu"] = relu(feats["m_prev_log"], tau_m_prev)
    X["q_prev_relu"] = relu(feats["q_prev_log"], tau_q_prev)
    X["brand_relu"] = relu(feats["brand_log"], tau_brand)
    X["p_relu"] = relu(feats["p_log"], tau_p)

    X["m_eff"] = feats["m_log"] + 0.5 * feats["m_prev_log"]
    X["q_eff"] = feats["q_log"] + 0.5 * feats["q_prev_log"]
    X["mi_after_q_prev"] = feats["mi_log"] * X["q_prev_step"]
    X["mi_after_m_prev"] = feats["mi_log"] * X["m_prev_step"]
    X["mi_after_brand"] = feats["mi_log"] * X["brand_step"]
    X["price_after_brand"] = feats["p_log"] * X["brand_step"]
    X["brand_after_q_prev"] = feats["brand_log"] * X["q_prev_step"]
    X["home_after_brand"] = feats["team24_home_shanghai"] * X["brand_step"]
    X["q_eff_x_mi"] = X["q_eff"] * feats["mi_log"]
    X["m_eff_x_q_eff"] = X["m_eff"] * X["q_eff"]
    X["brand_x_q_prev"] = feats["brand_log"] * feats["q_prev_log"]
    return pd.concat([context, X], axis=1)


def build_relative_threshold_matrix(feats, context, params):
    tau_p_rel, tau_m_rel, tau_q_rel, tau_mi_rel, tau_share = params
    X = pd.DataFrame(index=feats.index)

    X["price_rel_step"] = step(feats["price_vs_median_log"], tau_p_rel)
    X["m_rel_step"] = step(feats["m_vs_median_log"], tau_m_rel)
    X["q_rel_step"] = step(feats["q_vs_median_log"], tau_q_rel)
    X["mi_rel_step"] = step(feats["mi_vs_median_log"], tau_mi_rel)
    X["mi_share_step"] = step(feats["mi_share"], tau_share)

    X["price_rel_relu"] = relu(feats["price_vs_median_log"], tau_p_rel)
    X["m_rel_relu"] = relu(feats["m_vs_median_log"], tau_m_rel)
    X["q_rel_relu"] = relu(feats["q_vs_median_log"], tau_q_rel)
    X["mi_rel_relu"] = relu(feats["mi_vs_median_log"], tau_mi_rel)
    X["mi_share_relu"] = relu(feats["mi_share"], tau_share)

    X["price_rank_gate"] = feats["price_rank_pct"] * X["price_rel_step"]
    X["quality_rank_gate"] = feats["q_rank_pct"] * X["q_rel_step"]
    X["management_rank_gate"] = feats["m_rank_pct"] * X["m_rel_step"]
    X["market_rank_gate"] = feats["mi_rank_pct"] * X["mi_rel_step"]
    X["marketing_rank_gate"] = feats["marketing_rank_pct"] * X["mi_share_step"]
    X["agents_rank_gate"] = feats["agents_rank_pct"] * X["mi_share_step"]
    X["q_x_price_rel"] = feats["q_vs_median_log"] * feats["price_vs_median_log"]
    X["mi_x_price_rel"] = feats["mi_vs_median_log"] * feats["price_vs_median_log"]
    X["m_x_mi_rel"] = feats["m_vs_median_log"] * feats["mi_vs_median_log"]
    X["q_x_mi_rel"] = feats["q_vs_median_log"] * feats["mi_vs_median_log"]
    X["decision_power_gate"] = feats["decision_power"] * X["mi_share_step"]
    X["market_gate_power"] = feats["market_gate_power"]
    return pd.concat([context, X], axis=1)


def build_formula_matrix(feats, context):
    X = pd.DataFrame(index=feats.index)
    X["const"] = 1.0
    X["price_rank_pct"] = feats["price_rank_pct"]
    X["m_rank_pct"] = feats["m_rank_pct"]
    X["q_rank_pct"] = feats["q_rank_pct"]
    X["mi_rank_pct"] = feats["mi_rank_pct"]
    X["marketing_rank_pct"] = feats["marketing_rank_pct"]
    X["agents_rank_pct"] = feats["agents_rank_pct"]
    X["price_vs_median_log"] = feats["price_vs_median_log"]
    X["m_vs_median_log"] = feats["m_vs_median_log"]
    X["q_vs_median_log"] = feats["q_vs_median_log"]
    X["mi_vs_median_log"] = feats["mi_vs_median_log"]
    X["marketing_vs_median_log"] = feats["marketing_vs_median_log"]
    X["agents_vs_median_log"] = feats["agents_vs_median_log"]
    X["brand_log"] = feats["brand_log"]
    X["m_prev_log"] = feats["m_prev_log"]
    X["q_prev_log"] = feats["q_prev_log"]
    X["team24_home_shanghai"] = feats["team24_home_shanghai"]
    X["decision_power"] = feats["decision_power"]
    X["market_gate_power"] = feats["market_gate_power"]
    X["quality_price_synergy"] = feats["quality_price_synergy"]
    X["management_market_synergy"] = feats["management_market_synergy"]
    X["quality_market_synergy"] = feats["quality_market_synergy"]

    thresholds = {
        "price_rel": quantile_grid(feats["price_vs_median_log"], [0.3, 0.5, 0.7]),
        "m_rel": quantile_grid(feats["m_vs_median_log"], [0.3, 0.5, 0.7]),
        "q_rel": quantile_grid(feats["q_vs_median_log"], [0.3, 0.5, 0.7]),
        "mi_rel": quantile_grid(feats["mi_vs_median_log"], [0.3, 0.5, 0.7]),
        "mi_share": quantile_grid(feats["mi_share"], [0.4, 0.6, 0.8]),
        "price_rank": quantile_grid(feats["price_rank_pct"], [0.4, 0.6, 0.8]),
        "q_rank": quantile_grid(feats["q_rank_pct"], [0.4, 0.6, 0.8]),
    }

    for tau in thresholds["price_rel"]:
        X[f"price_rel_step_ge_{tau:.3f}"] = step(feats["price_vs_median_log"], tau)
    for tau in thresholds["m_rel"]:
        X[f"m_rel_step_ge_{tau:.3f}"] = step(feats["m_vs_median_log"], tau)
    for tau in thresholds["q_rel"]:
        X[f"q_rel_step_ge_{tau:.3f}"] = step(feats["q_vs_median_log"], tau)
    for tau in thresholds["mi_rel"]:
        X[f"mi_rel_step_ge_{tau:.3f}"] = step(feats["mi_vs_median_log"], tau)
    for tau in thresholds["mi_share"]:
        X[f"mi_share_step_ge_{tau:.3f}"] = step(feats["mi_share"], tau)
    for tau in thresholds["price_rank"]:
        X[f"price_rank_step_ge_{tau:.3f}"] = step(feats["price_rank_pct"], tau)
    for tau in thresholds["q_rank"]:
        X[f"q_rank_step_ge_{tau:.3f}"] = step(feats["q_rank_pct"], tau)

    X["price_x_quality"] = feats["price_vs_median_log"] * feats["q_vs_median_log"]
    X["price_x_market"] = feats["price_vs_median_log"] * feats["mi_vs_median_log"]
    X["management_x_market"] = feats["m_vs_median_log"] * feats["mi_vs_median_log"]
    X["quality_x_market"] = feats["q_vs_median_log"] * feats["mi_vs_median_log"]
    X["quality_x_price_rank"] = feats["q_rank_pct"] * feats["price_rank_pct"]
    X["market_x_price_rank"] = feats["mi_rank_pct"] * feats["price_rank_pct"]
    X["home_x_market"] = feats["team24_home_shanghai"] * feats["mi_rank_pct"]
    X["home_x_quality"] = feats["team24_home_shanghai"] * feats["q_rank_pct"]
    return X.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def weighted_ridge_fit(X, y, weights, ridge):
    X_mat = X.to_numpy(dtype=float)
    y_vec = np.asarray(y, dtype=float)
    w = np.sqrt(np.asarray(weights, dtype=float))
    Xw = X_mat * w[:, None]
    yw = y_vec * w
    beta = np.linalg.solve(Xw.T @ Xw + ridge * np.eye(Xw.shape[1]), Xw.T @ yw)
    pred = X_mat @ beta
    return beta, pred


def build_eval_context(df):
    team24_mask = df["is_team24"].to_numpy(dtype=bool)
    others_mask = (~team24_mask) & df["marketshare_clean"].notna().to_numpy(dtype=bool)
    others_reported_mask = (~team24_mask) & df["marketshare_reported"].notna().to_numpy(dtype=bool)
    overall_mask = df["fit_target"].notna().to_numpy(dtype=bool)

    keys = pd.MultiIndex.from_frame(df[["round", "market"]])
    group_codes, group_keys = pd.factorize(keys)
    group_util = (
        df.groupby(["round", "market"], sort=False)["market_utilization_clean"]
        .first()
        .reindex(group_keys)
        .to_numpy(dtype=float)
    )

    return {
        "team24_mask": team24_mask,
        "team24_actual": df.loc[team24_mask, "team24_real_cpi"].to_numpy(dtype=float),
        "others_mask": others_mask,
        "others_actual": df.loc[others_mask, "marketshare_clean"].to_numpy(dtype=float),
        "others_reported_mask": others_reported_mask,
        "others_reported_actual": df.loc[others_reported_mask, "marketshare_reported"].to_numpy(dtype=float),
        "overall_mask": overall_mask,
        "overall_actual": df.loc[overall_mask, "fit_target"].to_numpy(dtype=float),
        "group_codes": group_codes,
        "group_keys": group_keys,
        "group_actual": group_util,
    }


def evaluate_prediction(pred, eval_ctx):
    pred = np.asarray(pred, dtype=float)

    team24_pred = pred[eval_ctx["team24_mask"]]
    others_pred = pred[eval_ctx["others_mask"]]
    others_reported_pred = pred[eval_ctx["others_reported_mask"]]
    overall_pred = pred[eval_ctx["overall_mask"]]
    group_pred = np.bincount(eval_ctx["group_codes"], weights=pred, minlength=len(eval_ctx["group_keys"]))

    team24_m = metrics(eval_ctx["team24_actual"], team24_pred)
    others_m = metrics(eval_ctx["others_actual"], others_pred)
    others_reported_m = metrics(eval_ctx["others_reported_actual"], others_reported_pred)
    overall_m = metrics(eval_ctx["overall_actual"], overall_pred)
    group_m = metrics(eval_ctx["group_actual"], group_pred)
    max_pred = float(np.max(pred)) if len(pred) else 0.0

    return {
        "team24_metrics": team24_m,
        "others_metrics": others_m,
        "others_reported_metrics": others_reported_m,
        "overall_metrics": overall_m,
        "group_metrics": group_m,
        "group_pred": group_pred,
        "max_pred": max_pred,
    }


def score_prediction(eval_result):
    team24_m = eval_result["team24_metrics"]
    others_m = eval_result["others_metrics"]
    others_reported_m = eval_result["others_reported_metrics"]
    group_m = eval_result["group_metrics"]

    pen_team24 = max(0.95 - safe_r2(team24_m), 0.0)
    pen_others = max(0.90 - safe_r2(others_m), 0.0)
    pen_group = max(0.90 - safe_r2(group_m), 0.0)
    pen_reported = max(0.80 - safe_r2(others_reported_m), 0.0)
    pen_max = max(eval_result["max_pred"] - 0.25, 0.0)

    return (
        5.0 * team24_m["rmse"]
        + 1.75 * others_m["rmse"]
        + 1.25 * group_m["rmse"]
        + 0.75 * others_reported_m["rmse"]
        + 12.0 * pen_team24 ** 2
        + 6.0 * pen_others ** 2
        + 4.0 * pen_group ** 2
        + 2.0 * pen_reported ** 2
        + 5.0 * pen_max ** 2
    )


def prepare_model_table():
    teams = attach_lagged_features(load_market_reports()).copy()
    summaries_raw = load_summary_samples().copy()
    summaries = summaries_raw.copy()

    teams["round_original"] = teams["round"]
    summaries["round_original"] = summaries["round"]
    summaries_raw["round_original"] = summaries_raw["round"]

    # User-confirmed mapping:
    # - r-1 ... r6 are the actual fitted rounds.
    # - all-team market files currently named r7_* are actually round r6.
    # - r7_compeptiveindex_summary.xlsx is actual r6 Team24 CPI.
    # - r6_compeptiveindex_summary.xlsx is actual r7 Team24 CPI only.
    teams.loc[teams["round"] == "r7", "round"] = "r6"
    summaries.loc[summaries["round"] == "r7", "round"] = "r6"

    valid_rounds = ["r-1", "r1", "r2", "r3", "r4", "r5", "r6"]
    teams = teams[teams["round"].isin(valid_rounds)].copy()
    summaries = summaries[summaries["round"].isin(valid_rounds)].copy()
    summaries = summaries[~((summaries["round"] == "r6") & (summaries["round_original"] == "r6"))].copy()

    teams = clean_market_table(teams)

    samples = build_sample_table(teams, summaries)
    samples = samples[samples["team24_present_in_report"]].copy()
    samples["round_order"] = samples["round"].map(round_sort_key)
    samples = samples.sort_values(["round_order", "market"]).reset_index(drop=True)

    anchors = samples[["round", "market", "actual_competitiveness"]].rename(
        columns={"actual_competitiveness": "team24_actual_cpi"}
    )
    df = teams.merge(anchors, on=["round", "market"], how="inner")
    df["round_order"] = df["round"].map(round_sort_key)
    df = df.sort_values(["round_order", "market", "team"]).reset_index(drop=True)

    df["is_team24"] = df["team"] == TEAM_ID
    df["team24_real_cpi"] = np.where(df["is_team24"], df["team24_actual_cpi"], np.nan)
    df["proxy_cpi_target"] = np.where(
        df["is_team24"],
        df["team24_actual_cpi"],
        df["marketshare_clean"],
    )
    df["target_source"] = np.where(
        df["is_team24"],
        "team24_actual_cpi",
        "marketshare_clean_proxy",
    )
    df["fit_target"] = np.where(df["proxy_cpi_target"].notna(), np.maximum(df["proxy_cpi_target"], EPS), np.nan)
    df["is_labeled"] = df["fit_target"].notna()

    r7_summary = summaries_raw[summaries_raw["round"] == "r6"].copy()
    if not r7_summary.empty:
        team24_r6 = df[(df["round"] == "r6") & (df["is_team24"])].copy()
        if not team24_r6.empty:
            team24_r6 = team24_r6.set_index("market")
            synthetic_rows = []
            for _, srow in r7_summary.iterrows():
                market = srow["market"]
                if market not in team24_r6.index:
                    continue

                base = team24_r6.loc[market].copy()
                if isinstance(base, pd.DataFrame):
                    base = base.iloc[0].copy()

                agents = float(srow["team24_agents_summary"]) if pd.notna(srow["team24_agents_summary"]) else base["agents"]
                marketing = float(srow["team24_marketing_summary"]) if pd.notna(srow["team24_marketing_summary"]) else base["marketing_investment"]
                price = float(srow["team24_price_summary"]) if pd.notna(srow["team24_price_summary"]) else base["price"]
                sales_volume = float(srow["team24_sales_volume_summary"]) if pd.notna(srow["team24_sales_volume_summary"]) else np.nan
                actual_cpi = float(srow["actual_competitiveness"])

                new = base.copy()
                new["round"] = "r7"
                new["market"] = market
                new["round_original"] = "r6_summary_as_actual_r7"
                new["source_file"] = "r6_compeptiveindex_summary.xlsx"
                new["agents"] = agents
                new["marketing_investment"] = marketing
                new["market_index"] = (1 + 0.1 * agents) * marketing
                new["price"] = price
                new["avg_price_clean"] = base["avg_price_clean"]
                new["sales_volume"] = sales_volume
                new["market_share"] = np.nan
                new["marketshare_reported"] = np.nan
                new["marketshare_clean"] = np.nan
                new["market_size"] = float(base["market_size"]) * 1.1
                new["total_sales_volume"] = np.nan
                new["market_utilization_clean"] = np.nan
                new["team24_actual_cpi"] = actual_cpi
                new["team24_real_cpi"] = actual_cpi
                new["proxy_cpi_target"] = actual_cpi
                new["target_source"] = "team24_actual_cpi_r7"
                new["fit_target"] = max(actual_cpi, EPS)
                new["is_labeled"] = True
                new["is_team24"] = True
                new["prev_team_management_index"] = base["management_index"]
                new["prev_team_quality_index"] = base["quality_index"]
                new["prev_marketshare_clean"] = base["marketshare_clean"]
                new["prev_marketshare_reported"] = base["marketshare_reported"]
                synthetic_rows.append(new)

            if synthetic_rows:
                df = pd.concat([df, pd.DataFrame(synthetic_rows)], ignore_index=True, sort=False)

    df["round_order"] = df["round"].map(round_sort_key)
    df = df.sort_values(["round_order", "market", "team"]).reset_index(drop=True)
    return df


def family_configs(feats):
    return [
        {
            "name": "single_threshold_log_interact",
            "builder": build_single_threshold_matrix,
            "params_iter": product(
                quantile_grid(feats["m_log"], [0.2, 0.4, 0.6, 0.8]),
                quantile_grid(feats["q_log"], [0.2, 0.4, 0.6, 0.8]),
                quantile_grid(feats["mi_log"], [0.2, 0.4, 0.6, 0.8]),
                quantile_grid(feats["p_log"], [0.2, 0.4, 0.6, 0.8]),
            ),
        },
        {
            "name": "dual_threshold_log_gate",
            "builder": build_dual_threshold_matrix,
            "params_iter": product(
                threshold_pairs(feats["m_log"], [0.2, 0.4], [0.6, 0.8]),
                threshold_pairs(feats["q_log"], [0.2, 0.4], [0.6, 0.8]),
                threshold_pairs(feats["mi_log"], [0.2, 0.4], [0.6, 0.8]),
                threshold_pairs(feats["p_log"], [0.2, 0.4], [0.6, 0.8]),
            ),
        },
        {
            "name": "absolute_threshold_gate",
            "builder": build_absolute_gate_matrix,
            "params_iter": product(
                quantile_grid(feats["m_raw"], [0.15, 0.35, 0.55, 0.75]),
                quantile_grid(feats["q_raw"], [0.15, 0.35, 0.55, 0.75]),
                quantile_grid(feats["mi_raw"], [0.15, 0.35, 0.55, 0.75]),
                quantile_grid(feats["price_discount"], [0.2, 0.4, 0.6, 0.8]),
            ),
        },
        {
            "name": "semidynamic_threshold_gate",
            "builder": build_semidynamic_gate_matrix,
            "params_iter": product(
                quantile_grid(feats["m_prev_log"], [0.2, 0.4, 0.6, 0.8]),
                quantile_grid(feats["q_prev_log"], [0.2, 0.4, 0.6, 0.8]),
                quantile_grid(feats["brand_log"], [0.2, 0.4, 0.6, 0.8]),
                quantile_grid(feats["p_log"], [0.2, 0.4, 0.6, 0.8]),
            ),
        },
        {
            "name": "relative_threshold_gate",
            "builder": build_relative_threshold_matrix,
            "params_iter": product(
                quantile_grid(feats["price_vs_median_log"], [0.3, 0.5, 0.7]),
                quantile_grid(feats["m_vs_median_log"], [0.3, 0.5, 0.7]),
                quantile_grid(feats["q_vs_median_log"], [0.3, 0.5, 0.7]),
                quantile_grid(feats["mi_vs_median_log"], [0.3, 0.5, 0.7]),
                quantile_grid(feats["mi_share"], [0.4, 0.6, 0.8]),
            ),
        },
    ]


def params_to_text(params):
    if isinstance(params, tuple):
        return " | ".join(params_to_text(p) for p in params)
    if isinstance(params, float):
        return f"{params:.6f}"
    return str(params)


def model_accuracy_key(candidate):
    return (
        safe_r2(candidate["overall_metrics"]),
        safe_r2(candidate["others_metrics"]),
        safe_r2(candidate["team24_metrics"]),
        safe_r2(candidate["group_metrics"]),
        -candidate["score"],
    )


def fit_surrogate_formula(df, selected_model):
    feats = base_features(df)
    context = build_context(
        feats,
        sorted(df["round"].dropna().unique(), key=round_sort_key),
        sorted(df["market"].dropna().unique()),
    )
    X = build_formula_matrix(feats, context)
    pred = np.asarray(selected_model["pred"], dtype=float)
    y_log = np.log(np.maximum(pred, EPS))

    feature_cols = [c for c in X.columns if c != "const"]
    X_fit = X[feature_cols].to_numpy(dtype=float)

    best = None
    for depth in [3, 4, 5]:
        for min_leaf in [8, 12, 20]:
            reg = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=min_leaf, random_state=42)
            reg.fit(X_fit, y_log)
            pred_log = reg.predict(X_fit)
            pred_sur = np.maximum(np.exp(pred_log) - EPS, 0.0)
            fit_m = metrics(pred, pred_sur)
            leaves = int(reg.get_n_leaves())
            objective = (fit_m["r2"], -leaves, -fit_m["rmse"])
            if best is None or objective > best["objective"]:
                best = {
                    "alpha": depth,
                    "nnz": leaves,
                    "metrics_to_model": fit_m,
                    "pred": pred_sur,
                    "tree": reg,
                    "rules_text": export_text(reg, feature_names=feature_cols, decimals=4),
                    "feature_importance": pd.DataFrame(
                        {"feature": feature_cols, "importance": reg.feature_importances_}
                    ).sort_values("importance", ascending=False).reset_index(drop=True),
                    "objective": objective,
                }
    return best


def format_formula_term(name, coef):
    sign = "+" if coef >= 0 else "-"
    return f" {sign} {abs(coef):.6f}*{name}"


def surrogate_formula_lines(surrogate):
    lines = ["Approximate threshold rules for log(theoretical_cpi):"]
    lines.extend(surrogate["rules_text"].splitlines())
    lines.append("")
    lines.append("theoretical_cpi ~= exp(predicted_log_value_from_rules) - 1e-9")
    return lines


def search_best_model(df):
    feats = base_features(df)
    round_levels = sorted(df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    tree_X = build_tree_feature_matrix(feats, context)
    eval_ctx = build_eval_context(df)

    labeled_mask = df["is_labeled"].to_numpy(dtype=bool)
    y = df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float)
    y_log = np.log(np.maximum(y, EPS))

    ridge_values = [1e-6, 1e-4, 1e-2, 1e-1]
    team24_weights = [100.0, 300.0, 1000.0, 3000.0]

    best = None
    best_targeted = None
    rows = []
    candidate_models = []

    for family in family_configs(feats):
        for params in family["params_iter"]:
            X = family["builder"](feats, context, params)
            X_labeled = X.loc[labeled_mask]

            for ridge in ridge_values:
                for w24 in team24_weights:
                    weights = np.where(df.loc[labeled_mask, "is_team24"].to_numpy(dtype=bool), w24, 1.0)
                    beta, _ = weighted_ridge_fit(X_labeled, y_log, weights, ridge)
                    pred_log = X.to_numpy(dtype=float) @ beta
                    pred = np.exp(pred_log) - EPS
                    pred = np.maximum(pred, 0.0)

                    eval_result = evaluate_prediction(pred, eval_ctx)
                    score = score_prediction(eval_result)
                    row = {
                        "model_family": family["name"],
                        "params": params_to_text(params),
                        "team24_weight": w24,
                        "ridge": ridge,
                        "score": score,
                        "team24_r2": eval_result["team24_metrics"]["r2"],
                        "team24_rmse": eval_result["team24_metrics"]["rmse"],
                        "team24_mae": eval_result["team24_metrics"]["mae"],
                        "others_clean_r2": eval_result["others_metrics"]["r2"],
                        "others_clean_rmse": eval_result["others_metrics"]["rmse"],
                        "others_reported_r2": eval_result["others_reported_metrics"]["r2"],
                        "others_reported_rmse": eval_result["others_reported_metrics"]["rmse"],
                        "group_sum_r2": eval_result["group_metrics"]["r2"],
                        "group_sum_rmse": eval_result["group_metrics"]["rmse"],
                        "overall_r2": eval_result["overall_metrics"]["r2"],
                        "overall_rmse": eval_result["overall_metrics"]["rmse"],
                        "max_pred": eval_result["max_pred"],
                    }
                    rows.append(row)
                    candidate_models.append(
                        {
                            "model_type": "linear_ridge",
                            "name": family["name"],
                            "params": params,
                            "ridge": ridge,
                            "team24_weight": w24,
                            "columns": X.columns.tolist(),
                            "round_levels": round_levels,
                            "city_levels": city_levels,
                            "beta": beta,
                            "pred": pred.copy(),
                            "score": score,
                            **eval_result,
                        }
                    )

                    if best is None or score < best["score"]:
                        best = {
                            "model_type": "linear_ridge",
                            "name": family["name"],
                            "params": params,
                            "ridge": ridge,
                            "team24_weight": w24,
                            "columns": X.columns.tolist(),
                            "round_levels": round_levels,
                            "city_levels": city_levels,
                            "beta": beta,
                            "score": score,
                            **eval_result,
                        }
                    targeted_value = (
                        0.55 * safe_r2(eval_result["team24_metrics"])
                        + 0.35 * safe_r2(eval_result["others_metrics"])
                        + 0.10 * safe_r2(eval_result["group_metrics"])
                    )
                    if (
                        safe_r2(eval_result["team24_metrics"]) >= 0.95
                        and safe_r2(eval_result["others_metrics"]) >= 0.90
                        and (best_targeted is None or targeted_value > best_targeted["targeted_value"])
                    ):
                        best_targeted = {
                            "model_type": "linear_ridge",
                            "name": family["name"],
                            "params": params,
                            "ridge": ridge,
                            "team24_weight": w24,
                            "columns": X.columns.tolist(),
                            "round_levels": round_levels,
                            "city_levels": city_levels,
                            "beta": beta,
                            "score": score,
                            "targeted_value": targeted_value,
                            **eval_result,
                        }

    tree_weights = [100.0, 300.0, 1000.0]
    tree_X_labeled = tree_X.loc[labeled_mask]
    sample_weights_base = np.where(df.loc[labeled_mask, "is_team24"].to_numpy(dtype=bool), 1.0, 1.0)

    for w24 in tree_weights:
        sample_weights = np.where(df.loc[labeled_mask, "is_team24"].to_numpy(dtype=bool), w24, sample_weights_base)

        gbm_grid = product(
            [300, 500],
            [0.02, 0.05],
            [2, 3, 4],
            [2, 4],
            [0.7, 1.0],
        )
        for n_estimators, learning_rate, max_depth, min_samples_leaf, subsample in gbm_grid:
            estimator = GradientBoostingRegressor(
                loss="squared_error",
                n_estimators=n_estimators,
                learning_rate=learning_rate,
                max_depth=max_depth,
                min_samples_leaf=min_samples_leaf,
                subsample=subsample,
                random_state=42,
            )
            estimator.fit(tree_X_labeled, y_log, sample_weight=sample_weights)
            pred_log = estimator.predict(tree_X)
            pred = np.maximum(np.exp(pred_log) - EPS, 0.0)

            eval_result = evaluate_prediction(pred, eval_ctx)
            score = score_prediction(eval_result)
            row = {
                "model_family": "gradient_boosting_threshold_tree",
                "params": f"tw={w24}, n_estimators={n_estimators}, lr={learning_rate}, max_depth={max_depth}, leaf={min_samples_leaf}, subsample={subsample}",
                "team24_weight": w24,
                "ridge": np.nan,
                "score": score,
                "team24_r2": eval_result["team24_metrics"]["r2"],
                "team24_rmse": eval_result["team24_metrics"]["rmse"],
                "team24_mae": eval_result["team24_metrics"]["mae"],
                "others_clean_r2": eval_result["others_metrics"]["r2"],
                "others_clean_rmse": eval_result["others_metrics"]["rmse"],
                "others_reported_r2": eval_result["others_reported_metrics"]["r2"],
                "others_reported_rmse": eval_result["others_reported_metrics"]["rmse"],
                "group_sum_r2": eval_result["group_metrics"]["r2"],
                "group_sum_rmse": eval_result["group_metrics"]["rmse"],
                "overall_r2": eval_result["overall_metrics"]["r2"],
                "overall_rmse": eval_result["overall_metrics"]["rmse"],
                "max_pred": eval_result["max_pred"],
            }
            rows.append(row)
            candidate_models.append(
                {
                    "model_type": "sklearn_tree",
                    "name": "gradient_boosting_threshold_tree",
                    "params": row["params"],
                    "ridge": np.nan,
                    "team24_weight": w24,
                    "columns": tree_X.columns.tolist(),
                    "round_levels": round_levels,
                    "city_levels": city_levels,
                    "estimator": estimator,
                    "feature_importance": getattr(estimator, "feature_importances_", None),
                    "pred": pred.copy(),
                    "score": score,
                    **eval_result,
                }
            )

            if best is None or score < best["score"]:
                best = {
                    "model_type": "sklearn_tree",
                    "name": "gradient_boosting_threshold_tree",
                    "params": row["params"],
                    "ridge": np.nan,
                    "team24_weight": w24,
                    "columns": tree_X.columns.tolist(),
                    "round_levels": round_levels,
                    "city_levels": city_levels,
                    "estimator": estimator,
                    "feature_importance": getattr(estimator, "feature_importances_", None),
                    "score": score,
                    **eval_result,
                }
            targeted_value = (
                0.55 * safe_r2(eval_result["team24_metrics"])
                + 0.35 * safe_r2(eval_result["others_metrics"])
                + 0.10 * safe_r2(eval_result["group_metrics"])
            )
            if (
                safe_r2(eval_result["team24_metrics"]) >= 0.95
                and safe_r2(eval_result["others_metrics"]) >= 0.90
                and (best_targeted is None or targeted_value > best_targeted["targeted_value"])
            ):
                best_targeted = {
                    "model_type": "sklearn_tree",
                    "name": "gradient_boosting_threshold_tree",
                    "params": row["params"],
                    "ridge": np.nan,
                    "team24_weight": w24,
                    "columns": tree_X.columns.tolist(),
                    "round_levels": round_levels,
                    "city_levels": city_levels,
                    "estimator": estimator,
                    "feature_importance": getattr(estimator, "feature_importances_", None),
                    "score": score,
                    "targeted_value": targeted_value,
                    **eval_result,
                }

        rf_grid = product(
            [400],
            [8, 12, None],
            [1, 3, 5],
            ["sqrt", 0.5],
        )
        for n_estimators, max_depth, min_samples_leaf, max_features in rf_grid:
            estimator = RandomForestRegressor(
                n_estimators=n_estimators,
                max_depth=max_depth,
                min_samples_leaf=min_samples_leaf,
                max_features=max_features,
                random_state=42,
                n_jobs=-1,
            )
            estimator.fit(tree_X_labeled, y_log, sample_weight=sample_weights)
            pred_log = estimator.predict(tree_X)
            pred = np.maximum(np.exp(pred_log) - EPS, 0.0)

            eval_result = evaluate_prediction(pred, eval_ctx)
            score = score_prediction(eval_result)
            row = {
                "model_family": "random_forest_threshold_tree",
                "params": f"tw={w24}, n_estimators={n_estimators}, max_depth={max_depth}, leaf={min_samples_leaf}, max_features={max_features}",
                "team24_weight": w24,
                "ridge": np.nan,
                "score": score,
                "team24_r2": eval_result["team24_metrics"]["r2"],
                "team24_rmse": eval_result["team24_metrics"]["rmse"],
                "team24_mae": eval_result["team24_metrics"]["mae"],
                "others_clean_r2": eval_result["others_metrics"]["r2"],
                "others_clean_rmse": eval_result["others_metrics"]["rmse"],
                "others_reported_r2": eval_result["others_reported_metrics"]["r2"],
                "others_reported_rmse": eval_result["others_reported_metrics"]["rmse"],
                "group_sum_r2": eval_result["group_metrics"]["r2"],
                "group_sum_rmse": eval_result["group_metrics"]["rmse"],
                "overall_r2": eval_result["overall_metrics"]["r2"],
                "overall_rmse": eval_result["overall_metrics"]["rmse"],
                "max_pred": eval_result["max_pred"],
            }
            rows.append(row)
            candidate_models.append(
                {
                    "model_type": "sklearn_tree",
                    "name": "random_forest_threshold_tree",
                    "params": row["params"],
                    "ridge": np.nan,
                    "team24_weight": w24,
                    "columns": tree_X.columns.tolist(),
                    "round_levels": round_levels,
                    "city_levels": city_levels,
                    "estimator": estimator,
                    "feature_importance": getattr(estimator, "feature_importances_", None),
                    "pred": pred.copy(),
                    "score": score,
                    **eval_result,
                }
            )

            if best is None or score < best["score"]:
                best = {
                    "model_type": "sklearn_tree",
                    "name": "random_forest_threshold_tree",
                    "params": row["params"],
                    "ridge": np.nan,
                    "team24_weight": w24,
                    "columns": tree_X.columns.tolist(),
                    "round_levels": round_levels,
                    "city_levels": city_levels,
                    "estimator": estimator,
                    "feature_importance": getattr(estimator, "feature_importances_", None),
                    "score": score,
                    **eval_result,
                }
            targeted_value = (
                0.55 * safe_r2(eval_result["team24_metrics"])
                + 0.35 * safe_r2(eval_result["others_metrics"])
                + 0.10 * safe_r2(eval_result["group_metrics"])
            )
            if (
                safe_r2(eval_result["team24_metrics"]) >= 0.95
                and safe_r2(eval_result["others_metrics"]) >= 0.90
                and (best_targeted is None or targeted_value > best_targeted["targeted_value"])
            ):
                best_targeted = {
                    "model_type": "sklearn_tree",
                    "name": "random_forest_threshold_tree",
                    "params": row["params"],
                    "ridge": np.nan,
                    "team24_weight": w24,
                    "columns": tree_X.columns.tolist(),
                    "round_levels": round_levels,
                    "city_levels": city_levels,
                    "estimator": estimator,
                    "feature_importance": getattr(estimator, "feature_importances_", None),
                    "score": score,
                    "targeted_value": targeted_value,
                    **eval_result,
                }

    ranked_candidates = sorted(
        candidate_models,
        key=lambda m: (
            -safe_r2(m["team24_metrics"]),
            -safe_r2(m["others_metrics"]),
            -safe_r2(m["group_metrics"]),
            m["score"],
        ),
    )[:12]

    ensemble_rows = []
    ensemble_weights = [0.2, 0.35, 0.5, 0.65, 0.8]
    for idx1 in range(len(ranked_candidates)):
        for idx2 in range(idx1 + 1, len(ranked_candidates)):
            m1 = ranked_candidates[idx1]
            m2 = ranked_candidates[idx2]
            for alpha in ensemble_weights:
                pred = alpha * m1["pred"] + (1.0 - alpha) * m2["pred"]
                eval_result = evaluate_prediction(pred, eval_ctx)
                score = score_prediction(eval_result)
                params = f"{alpha:.2f}*[{m1['name']}::{m1['params']}] + {1.0-alpha:.2f}*[{m2['name']}::{m2['params']}]"
                row = {
                    "model_family": "blended_threshold_ensemble",
                    "params": params,
                    "team24_weight": np.nan,
                    "ridge": np.nan,
                    "score": score,
                    "team24_r2": eval_result["team24_metrics"]["r2"],
                    "team24_rmse": eval_result["team24_metrics"]["rmse"],
                    "team24_mae": eval_result["team24_metrics"]["mae"],
                    "others_clean_r2": eval_result["others_metrics"]["r2"],
                    "others_clean_rmse": eval_result["others_metrics"]["rmse"],
                    "others_reported_r2": eval_result["others_reported_metrics"]["r2"],
                    "others_reported_rmse": eval_result["others_reported_metrics"]["rmse"],
                    "group_sum_r2": eval_result["group_metrics"]["r2"],
                    "group_sum_rmse": eval_result["group_metrics"]["rmse"],
                    "overall_r2": eval_result["overall_metrics"]["r2"],
                    "overall_rmse": eval_result["overall_metrics"]["rmse"],
                    "max_pred": eval_result["max_pred"],
                }
                ensemble_rows.append(row)
                candidate_models.append(
                    {
                        "model_type": "ensemble",
                        "name": "blended_threshold_ensemble",
                        "params": params,
                        "team24_weight": np.nan,
                        "ridge": np.nan,
                        "columns": [],
                        "round_levels": round_levels,
                        "city_levels": city_levels,
                        "pred": pred.copy(),
                        "members": [m1, m2],
                        "weights": [alpha, 1.0 - alpha],
                        "score": score,
                        **eval_result,
                    }
                )
                if best is None or score < best["score"]:
                    best = {
                        "model_type": "ensemble",
                        "name": "blended_threshold_ensemble",
                        "params": params,
                        "team24_weight": np.nan,
                        "ridge": np.nan,
                        "columns": [],
                        "round_levels": round_levels,
                        "city_levels": city_levels,
                        "pred": pred.copy(),
                        "members": [m1, m2],
                        "weights": [alpha, 1.0 - alpha],
                        "score": score,
                        **eval_result,
                    }
                targeted_value = (
                    0.55 * safe_r2(eval_result["team24_metrics"])
                    + 0.35 * safe_r2(eval_result["others_metrics"])
                    + 0.10 * safe_r2(eval_result["group_metrics"])
                )
                if (
                    safe_r2(eval_result["team24_metrics"]) >= 0.95
                    and safe_r2(eval_result["others_metrics"]) >= 0.90
                    and (best_targeted is None or targeted_value > best_targeted["targeted_value"])
                ):
                    best_targeted = {
                        "model_type": "ensemble",
                        "name": "blended_threshold_ensemble",
                        "params": params,
                        "team24_weight": np.nan,
                        "ridge": np.nan,
                        "columns": [],
                        "round_levels": round_levels,
                        "city_levels": city_levels,
                        "pred": pred.copy(),
                        "members": [m1, m2],
                        "weights": [alpha, 1.0 - alpha],
                        "score": score,
                        "targeted_value": targeted_value,
                        **eval_result,
                    }

    rows.extend(ensemble_rows)

    results_df = pd.DataFrame(rows).sort_values(
        ["score", "team24_r2", "others_clean_r2", "group_sum_r2"],
        ascending=[True, False, False, False],
    ).reset_index(drop=True)
    accuracy_sorted = sorted(candidate_models, key=model_accuracy_key, reverse=True)
    selected = accuracy_sorted[0]
    selected["selection_rule"] = "highest_accuracy"
    return selected, results_df


def apply_model(df, model):
    feats = base_features(df)
    context = build_context(feats, model["round_levels"], model["city_levels"])
    if model["model_type"] == "linear_ridge":
        builders = {
            "single_threshold_log_interact": build_single_threshold_matrix,
            "dual_threshold_log_gate": build_dual_threshold_matrix,
            "absolute_threshold_gate": build_absolute_gate_matrix,
            "semidynamic_threshold_gate": build_semidynamic_gate_matrix,
            "relative_threshold_gate": build_relative_threshold_matrix,
        }
        X = builders[model["name"]](feats, context, model["params"])
        X = X.reindex(columns=model["columns"], fill_value=0.0)
        pred_log = X.to_numpy(dtype=float) @ model["beta"]
    elif model["model_type"] == "ensemble":
        X = pd.DataFrame(index=feats.index)
        pred = np.asarray(model["pred"], dtype=float)
        pred_log = np.log(np.maximum(pred, EPS))
    else:
        X = build_tree_feature_matrix(feats, context)
        X = X.reindex(columns=model["columns"], fill_value=0.0)
        pred_log = model["estimator"].predict(X)

    pred_log, residual_log_shift = apply_stage1_residual_calibration(
        pred_log,
        feats["market"],
        prev_marketshare_clean=feats["prev_marketshare_clean"],
        prev_market_utilization_clean=feats["prev_market_utilization_clean"],
    )

    if model["model_type"] != "ensemble":
        pred = np.maximum(np.exp(pred_log) - EPS, 0.0)

    out = feats.copy()
    out["marketshare_reported"] = df["marketshare_reported"].to_numpy(dtype=float)
    out["marketshare_clean"] = df["marketshare_clean"].to_numpy(dtype=float)
    out["theoretical_cpi"] = pred
    out["theoretical_cpi_log"] = pred_log
    out["stage1_residual_log_shift"] = residual_log_shift
    out["team24_real_cpi"] = df["team24_real_cpi"].to_numpy(dtype=float)
    out["proxy_cpi_target"] = df["proxy_cpi_target"].to_numpy(dtype=float)
    out["target_source"] = df["target_source"].to_numpy(dtype=object)
    out["is_team24"] = df["is_team24"].to_numpy(dtype=bool)
    out["cpi_fit_error_to_target"] = out["theoretical_cpi"] - out["proxy_cpi_target"]
    out["cpi_abs_error_to_target"] = out["cpi_fit_error_to_target"].abs()
    out["cpi_fit_error_to_reported_marketshare"] = out["theoretical_cpi"] - out["marketshare_reported"]
    out["cpi_abs_error_to_reported_marketshare"] = out["cpi_fit_error_to_reported_marketshare"].abs()
    out["predicted_group_cpi_sum"] = out.groupby(["round", "market"])["theoretical_cpi"].transform("sum")
    out["actual_group_clean_sum"] = out.groupby(["round", "market"])["market_utilization_clean"].transform("first")
    out["group_sum_gap"] = out["predicted_group_cpi_sum"] - out["actual_group_clean_sum"]
    return out, X


def main():
    df = prepare_model_table()
    model, comparison_df = search_best_model(df)
    scored, _ = apply_model(df, model)
    surrogate = fit_surrogate_formula(df, model)

    eval_ctx = build_eval_context(scored)
    final_eval = evaluate_prediction(scored["theoretical_cpi"].to_numpy(dtype=float), eval_ctx)
    surrogate_eval = evaluate_prediction(np.asarray(surrogate["pred"], dtype=float), eval_ctx)

    scored["theoretical_cpi_rank"] = scored.groupby(["round", "market"])["theoretical_cpi"].rank(method="min", ascending=False)
    scored["marketshare_clean_rank"] = scored.groupby(["round", "market"])["marketshare_clean"].rank(method="min", ascending=False)
    scored["marketshare_reported_rank"] = scored.groupby(["round", "market"])["marketshare_reported"].rank(method="min", ascending=False)
    scored["rank_gap_vs_marketshare_clean"] = scored["theoretical_cpi_rank"] - scored["marketshare_clean_rank"]
    scored["rank_gap_vs_marketshare_reported"] = scored["theoretical_cpi_rank"] - scored["marketshare_reported_rank"]

    scored = scored.sort_values(
        ["round_order", "market", "theoretical_cpi_rank", "team"],
        ascending=[True, True, True, True],
    ).reset_index(drop=True)

    team24_fit = scored[scored["is_team24"]][
        [
            "round",
            "market",
            "marketshare_clean",
            "marketshare_reported",
            "theoretical_cpi",
            "team24_real_cpi",
            "cpi_fit_error_to_target",
            "cpi_abs_error_to_target",
            "predicted_group_cpi_sum",
            "actual_group_clean_sum",
        ]
    ].copy()

    metrics_df = pd.DataFrame([{
        "model": model["name"],
        "goal": "fit Team24 actual CPI strongly; use sales_volume/market_size as peer CPI proxy; compare with reported marketshare",
        "selection_rule": model.get("selection_rule"),
        "team24_weight": model["team24_weight"],
        "ridge": model["ridge"],
        "params": params_to_text(model["params"]),
        "score": model["score"],
        "overall_mae": final_eval["overall_metrics"]["mae"],
        "overall_rmse": final_eval["overall_metrics"]["rmse"],
        "overall_r2": final_eval["overall_metrics"]["r2"],
        "team24_mae": final_eval["team24_metrics"]["mae"],
        "team24_rmse": final_eval["team24_metrics"]["rmse"],
        "team24_r2": final_eval["team24_metrics"]["r2"],
        "team24_corr": final_eval["team24_metrics"]["corr"],
        "others_clean_mae": final_eval["others_metrics"]["mae"],
        "others_clean_rmse": final_eval["others_metrics"]["rmse"],
        "others_clean_r2": final_eval["others_metrics"]["r2"],
        "others_reported_mae": final_eval["others_reported_metrics"]["mae"],
        "others_reported_rmse": final_eval["others_reported_metrics"]["rmse"],
        "others_reported_r2": final_eval["others_reported_metrics"]["r2"],
        "group_sum_mae": final_eval["group_metrics"]["mae"],
        "group_sum_rmse": final_eval["group_metrics"]["rmse"],
        "group_sum_r2": final_eval["group_metrics"]["r2"],
        "max_pred": final_eval["max_pred"],
        "surrogate_alpha": surrogate["alpha"],
        "surrogate_terms": surrogate["nnz"],
        "surrogate_to_model_r2": surrogate["metrics_to_model"]["r2"],
        "surrogate_to_model_rmse": surrogate["metrics_to_model"]["rmse"],
        "surrogate_team24_r2": surrogate_eval["team24_metrics"]["r2"],
        "surrogate_others_clean_r2": surrogate_eval["others_metrics"]["r2"],
        "surrogate_overall_r2": surrogate_eval["overall_metrics"]["r2"],
    }])
    if model["model_type"] == "linear_ridge":
        coef_df = pd.DataFrame({"feature": model["columns"], "coef": model["beta"]})
    elif model["model_type"] == "ensemble":
        coef_df = pd.DataFrame(
            [
                {
                    "component_weight": weight,
                    "component_model": member["name"],
                    "component_params": member["params"],
                    "component_team24_weight": member.get("team24_weight"),
                    "component_score": member["score"],
                    "component_team24_r2": member["team24_metrics"]["r2"],
                    "component_others_clean_r2": member["others_metrics"]["r2"],
                }
                for weight, member in zip(model["weights"], model["members"])
            ]
        )
    else:
        importance = model.get("feature_importance")
        coef_df = pd.DataFrame(
            {
                "feature": model["columns"],
                "importance": importance if importance is not None else np.nan,
            }
        ).sort_values("importance", ascending=False, na_position="last").reset_index(drop=True)
    top_models_df = comparison_df.head(200).copy()
    formula_lines = surrogate_formula_lines(surrogate)
    formula_df = pd.DataFrame({"formula_line": formula_lines})
    surrogate_coef_df = surrogate["feature_importance"].copy()

    output_xlsx = writable_path(OUTPUT_XLSX)
    model_xlsx = writable_path(MODEL_XLSX)

    left_cols = [
        "round",
        "market",
        "team",
        "marketshare_clean",
        "theoretical_cpi",
        "team24_real_cpi",
        "marketshare_reported",
        "proxy_cpi_target",
        "target_source",
        "theoretical_cpi_rank",
        "marketshare_clean_rank",
        "marketshare_reported_rank",
        "rank_gap_vs_marketshare_clean",
        "rank_gap_vs_marketshare_reported",
    ]
    detail_cols = [
        "management_index",
        "quality_index",
        "agents",
        "marketing_investment",
        "market_index",
        "price",
        "avg_price",
        "avg_price_clean",
        "sales_volume",
        "market_size",
        "total_sales_volume",
        "market_utilization_clean",
        "predicted_group_cpi_sum",
        "actual_group_clean_sum",
        "group_sum_gap",
        "m_log",
        "q_log",
        "mi_log",
        "p_log",
        "m_prev_log",
        "q_prev_log",
        "brand_log",
        "theoretical_cpi_log",
        "cpi_fit_error_to_target",
        "cpi_abs_error_to_target",
        "cpi_fit_error_to_reported_marketshare",
        "cpi_abs_error_to_reported_marketshare",
        "round_original",
        "source_file",
        "is_team24",
    ]
    all_cols = left_cols + detail_cols

    with pd.ExcelWriter(output_xlsx, engine="openpyxl") as writer:
        scored[all_cols].to_excel(writer, sheet_name="all_teams_r1_r7", index=False)
        scored[~scored["is_team24"]][all_cols].to_excel(writer, sheet_name="other_teams_only", index=False)
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        coef_df.to_excel(writer, sheet_name="coefficients", index=False)
        team24_fit.to_excel(writer, sheet_name="team24_fit", index=False)
        top_models_df.to_excel(writer, sheet_name="top_models", index=False)
        formula_df.to_excel(writer, sheet_name="formula", index=False)
        surrogate_coef_df.to_excel(writer, sheet_name="formula_coeffs", index=False)
        for round_name in ["r-1", "r1", "r2", "r3", "r4", "r5", "r6", "r7"]:
            scored[scored["round"] == round_name][all_cols].to_excel(writer, sheet_name=round_name, index=False)

    with pd.ExcelWriter(model_xlsx, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        coef_df.to_excel(writer, sheet_name="coefficients", index=False)
        team24_fit.to_excel(writer, sheet_name="team24_fit", index=False)
        top_models_df.to_excel(writer, sheet_name="top_models", index=False)
        formula_df.to_excel(writer, sheet_name="formula", index=False)
        surrogate_coef_df.to_excel(writer, sheet_name="formula_coeffs", index=False)

    report = [
        "# Global Constrained Theoretical CPI Model",
        "",
        "- peer CPI proxy: marketshare_clean = sales_volume / market_size",
        "- comparison proxy: marketshare_reported from source market report",
        "- Team24 rows use actual CPI labels for r-1..r7",
        f"- best model family: {model['name']}",
        f"- selection rule: {model.get('selection_rule')}",
        f"- best params: {params_to_text(model['params'])}",
        "",
        "## Approximate Readable Formula",
        *formula_lines,
        "",
        "## Metrics",
        f"- Team24 R2 = {final_eval['team24_metrics']['r2']:.8f}",
        f"- Team24 RMSE = {final_eval['team24_metrics']['rmse']:.8f}",
        f"- Others clean-share R2 = {final_eval['others_metrics']['r2']:.8f}",
        f"- Others reported-share R2 = {final_eval['others_reported_metrics']['r2']:.8f}",
        f"- Group-sum R2 = {final_eval['group_metrics']['r2']:.8f}",
        f"- Surrogate-to-model R2 = {surrogate['metrics_to_model']['r2']:.8f}",
        "",
        f"- Output workbook: {output_xlsx}",
        f"- Model workbook: {model_xlsx}",
    ]
    OUTPUT_MD.write_text("\n".join(report), encoding="utf-8")

    print(f"Saved: {output_xlsx}")
    print(f"Saved: {model_xlsx}")
    print(metrics_df.to_string(index=False))
    print(top_models_df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
