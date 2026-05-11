#!/usr/bin/env python3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from analyze_team24_competitiveness import (
    TEAM_ID,
    build_sample_table,
    load_market_reports,
    load_summary_samples,
    round_sort_key,
)
from fit_team24_semidynamic_model import attach_lagged_features


BASE_DIR = Path("/mnt/c/Users/david/documents/ASDAN/表格/WYEF_results")
OUTPUT_XLSX = BASE_DIR / "global_constrained_cpi_model.xlsx"
PEER_XLSX = BASE_DIR / "r1_r6_peer_global_constrained_cpi.xlsx"
OUTPUT_MD = BASE_DIR / "global_constrained_cpi_model.md"
ROUND_REMAP = {"r6": "r7", "r7": "r6"}
EPS = 1e-9


def remap_round(round_name):
    return ROUND_REMAP.get(round_name, round_name)


def soft_threshold(x, tau, sharpness=10.0):
    z = sharpness * (x - tau)
    return np.logaddexp(0.0, z) / sharpness


def prepare_data():
    teams = attach_lagged_features(load_market_reports()).copy()
    summaries = load_summary_samples().copy()

    teams["round_original"] = teams["round"]
    summaries["round_original"] = summaries["round"]
    teams["round"] = teams["round"].map(remap_round)
    summaries["round"] = summaries["round"].map(remap_round)

    teams = teams[teams["round"].isin(["r1", "r2", "r3", "r4", "r5", "r6"])].copy()
    samples = build_sample_table(teams, summaries)
    samples = samples[samples["team24_present_in_report"]].copy()
    samples = samples[samples["round"].isin(["r1", "r2", "r3", "r4", "r5", "r6"])].copy()
    samples["round_order"] = samples["round"].map(round_sort_key)
    samples = samples.sort_values(["round_order", "market"]).reset_index(drop=True)

    anchors = samples[["round", "market", "actual_competitiveness"]].rename(
        columns={"actual_competitiveness": "team24_actual_cpi"}
    )
    teams = teams.merge(anchors, on=["round", "market"], how="inner")

    price_fix_mask = teams["price"].fillna(0).between(0.01, 999.999) & (teams["avg_price"].fillna(0) > 5000)
    teams.loc[price_fix_mask, "price"] = teams.loc[price_fix_mask, "price"] * 1000.0

    teams["sales_share_raw"] = (
        teams["sales_volume"].clip(lower=0).fillna(0) / teams["total_sales_volume"].replace(0, np.nan)
    ).fillna(0)
    teams["market_share_clean"] = teams["market_share"].clip(lower=0).fillna(0)
    teams["abs_share_from_market_size"] = (
        teams["sales_volume"].clip(lower=0).fillna(0) / teams["market_size"].replace(0, np.nan)
    ).fillna(np.nan)

    share_target = pd.Series(index=teams.index, dtype=float)
    share_source = pd.Series(index=teams.index, dtype=object)
    for _, group in teams.groupby(["round", "market"], sort=False):
        market_size_share = group["abs_share_from_market_size"].to_numpy(dtype=float)
        market_share = group["market_share_clean"].to_numpy(dtype=float)

        if np.isfinite(market_size_share).sum() > 0:
            target = np.nan_to_num(market_size_share, nan=0.0)
            source = "sales_volume_over_market_size"
        elif np.nansum(market_share) > 0:
            target = np.nan_to_num(market_share, nan=0.0)
            source = "reported_market_share"
        else:
            target = np.zeros(len(group), dtype=float)
            source = "uniform_fallback"

        share_target.loc[group.index] = target
        share_source.loc[group.index] = source

    teams["share_target"] = share_target.to_numpy(dtype=float)
    teams["share_target_source"] = share_source.astype(str)
    teams["round_order"] = teams["round"].map(round_sort_key)
    teams = teams.sort_values(["round_order", "market", "team"]).reset_index(drop=True)
    return teams, samples


def build_model_data(teams):
    df = teams.copy()

    df["m_cur"] = np.log1p(df["management_index"].fillna(0).clip(lower=0))
    df["q_cur"] = np.log1p(df["quality_index"].fillna(0).clip(lower=0))
    df["mi_cur"] = np.log1p(df["market_index"].fillna(0).clip(lower=0))
    df["m_prev"] = np.log1p(df["prev_team_management_index"].fillna(0).clip(lower=0))
    df["q_prev"] = np.log1p(df["prev_team_quality_index"].fillna(0).clip(lower=0))
    df["brand"] = np.log1p(df["prev_market_share"].fillna(0).clip(lower=0) * 1000.0)

    price_ratio = (df["avg_price"].fillna(1) / df["price"].replace(0, np.nan)).fillna(1.0)
    df["price_adv"] = np.log(np.maximum(price_ratio, 1e-9))

    scales = {}
    for col in ["m_cur", "q_cur", "mi_cur", "m_prev", "q_prev", "brand"]:
        scale = float(df[col].quantile(0.95))
        scales[col] = max(scale, 1.0)
        df[f"{col}_scaled"] = np.clip(df[col] / scales[col], 0.0, 2.0)

    price_scale = float(np.quantile(np.abs(df["price_adv"].fillna(0)), 0.95))
    scales["price_adv"] = max(price_scale, 0.2)
    df["price_adv_scaled"] = np.clip(df["price_adv"] / scales["price_adv"], -2.0, 2.0)

    df["team24_home_shanghai"] = ((df["team"] == TEAM_ID) & (df["market"] == "Shanghai")).astype(float)

    group_slices = []
    team24_idx = []
    for _, group in df.groupby(["round", "market"], sort=False):
        idx = group.index.to_numpy(dtype=int)
        team24_local = np.where(group["team"].to_numpy() == TEAM_ID)[0]
        if len(team24_local) != 1:
            continue
        group_slices.append(idx)
        team24_idx.append(int(idx[team24_local[0]]))

    return df, scales, group_slices, team24_idx


def unpack_params(params):
    return {
        "w_proxy_sqrt": params[0],
        "w_proxy": params[1],
        "w_m": params[2],
        "w_q": params[3],
        "w_mi": params[4],
        "w_p": params[5],
        "w_mq": params[6],
        "w_qmi": params[7],
        "w_brand": params[8],
        "w_home": params[9],
        "rho_m": params[10],
        "rho_q": params[11],
        "tau_m": params[12],
        "tau_q": params[13],
        "tau_mi": params[14],
        "tau_p": params[15],
        "floor": params[16],
        "eta": params[17],
    }


def evaluate(df, group_slices, team24_idx, params):
    p = unpack_params(params)

    share_proxy = df["market_share_clean"].to_numpy(dtype=float)
    share_sqrt = np.sqrt(np.maximum(share_proxy, 0))
    m_eff = df["m_cur_scaled"].to_numpy(dtype=float) + p["rho_m"] * df["m_prev_scaled"].to_numpy(dtype=float)
    q_eff = df["q_cur_scaled"].to_numpy(dtype=float) + p["rho_q"] * df["q_prev_scaled"].to_numpy(dtype=float)
    mi_eff = df["mi_cur_scaled"].to_numpy(dtype=float)
    brand_eff = df["brand_scaled"].to_numpy(dtype=float)
    price_eff = df["price_adv_scaled"].to_numpy(dtype=float)
    home = df["team24_home_shanghai"].to_numpy(dtype=float)

    m_thr = soft_threshold(m_eff, p["tau_m"])
    q_thr = soft_threshold(q_eff, p["tau_q"])
    mi_thr = soft_threshold(mi_eff, p["tau_mi"])
    p_thr = soft_threshold(price_eff, p["tau_p"])

    term_m = p["w_m"] * m_thr
    term_q = p["w_q"] * q_thr
    term_mi = p["w_mi"] * mi_thr
    term_p = p["w_p"] * p_thr
    term_mq = p["w_mq"] * m_thr * q_thr
    term_qmi = p["w_qmi"] * q_thr * mi_thr
    term_brand = p["w_brand"] * brand_eff
    term_home = p["w_home"] * home

    raw_score = (
        p["floor"]
        + p["w_proxy_sqrt"] * share_sqrt
        + p["w_proxy"] * share_proxy
        + term_m
        + term_q
        + term_mi
        + term_p
        + term_mq
        + term_qmi
        + term_brand
        + term_home
    )
    raw_score = np.maximum(raw_score, EPS)
    share_basis = np.power(raw_score, p["eta"])

    predicted_share = np.zeros(len(df), dtype=float)
    predicted_share_norm = np.zeros(len(df), dtype=float)
    estimated_cpi = np.zeros(len(df), dtype=float)
    group_scale = np.zeros(len(df), dtype=float)
    group_target_total = np.zeros(len(df), dtype=float)
    group_pred_total = np.zeros(len(df), dtype=float)
    group_spreads = []

    team24_cpi = df["team24_actual_cpi"].to_numpy(dtype=float)
    target_abs = df["share_target"].to_numpy(dtype=float)
    for idx, team24_row in zip(group_slices, team24_idx):
        group_basis = share_basis[idx]
        base_24 = max(share_basis[team24_row], EPS)
        cpi_ratio = group_basis / base_24
        estimated_cpi[idx] = team24_cpi[team24_row] * cpi_ratio

        scale = target_abs[team24_row] / max(team24_cpi[team24_row] ** p["eta"], EPS)
        predicted_abs = scale * np.power(np.maximum(estimated_cpi[idx], EPS), p["eta"])
        predicted_share[idx] = predicted_abs
        predicted_share_norm[idx] = predicted_abs / max(predicted_abs.sum(), EPS)
        group_scale[idx] = scale
        group_target_total[idx] = target_abs[idx].sum()
        group_pred_total[idx] = predicted_abs.sum()
        group_spreads.append(float(np.log(group_basis.max() + EPS) - np.log(group_basis.min() + EPS)))

    return {
        "predicted_share": predicted_share,
        "predicted_share_norm": predicted_share_norm,
        "estimated_cpi": estimated_cpi,
        "raw_score": raw_score,
        "share_basis": share_basis,
        "group_scale": group_scale,
        "group_target_total": group_target_total,
        "group_pred_total": group_pred_total,
        "term_share_sqrt": p["w_proxy_sqrt"] * share_sqrt,
        "term_share": p["w_proxy"] * share_proxy,
        "group_spreads": np.array(group_spreads, dtype=float),
        "m_thr": m_thr,
        "q_thr": q_thr,
        "mi_thr": mi_thr,
        "p_thr": p_thr,
        "term_m": term_m,
        "term_q": term_q,
        "term_mi": term_mi,
        "term_p": term_p,
        "term_mq": term_mq,
        "term_qmi": term_qmi,
        "term_brand": term_brand,
        "term_home": term_home,
    }


def objective(params, df, group_slices, team24_idx):
    out = evaluate(df, group_slices, team24_idx, params)

    target = df["share_target"].to_numpy(dtype=float)
    pred = out["predicted_share"]
    is_team24 = (df["team"] == TEAM_ID).to_numpy(dtype=float)

    weights = 1.0 + 3.0 * is_team24
    mse_loss = np.mean(weights * (pred - target) ** 2)
    log_loss = np.mean(weights * (np.log(pred + 1e-6) - np.log(target + 1e-6)) ** 2)

    spread_penalty = np.mean(np.maximum(out["group_spreads"] - 4.0, 0.0) ** 2)
    total_penalty = np.mean((out["group_pred_total"] - out["group_target_total"]) ** 2)

    p = unpack_params(params)
    reg = 0.01 * (
        0.5 * p["w_proxy_sqrt"] ** 2
        + 0.5 * p["w_proxy"] ** 2
        + p["w_m"] ** 2
        + p["w_q"] ** 2
        + p["w_mi"] ** 2
        + p["w_p"] ** 2
        + 0.5 * p["w_mq"] ** 2
        + 0.5 * p["w_qmi"] ** 2
        + 0.25 * p["w_brand"] ** 2
        + 0.25 * p["w_home"] ** 2
    )

    return float(0.52 * mse_loss + 0.18 * log_loss + 0.20 * total_penalty + 0.07 * spread_penalty + 0.03 * reg)


def fit_model(df, group_slices, team24_idx):
    bounds = [
        (0.0, 5.0),   # w_proxy_sqrt
        (0.0, 5.0),   # w_proxy
        (0.0, 3.0),   # w_m
        (0.0, 3.0),   # w_q
        (0.0, 3.0),   # w_mi
        (0.0, 3.0),   # w_p
        (0.0, 4.0),   # w_mq
        (0.0, 4.0),   # w_qmi
        (0.0, 1.5),   # w_brand
        (0.0, 2.0),   # w_home
        (0.0, 1.0),   # rho_m
        (0.0, 1.0),   # rho_q
        (0.0, 1.2),   # tau_m
        (0.0, 1.2),   # tau_q
        (0.0, 1.2),   # tau_mi
        (-0.8, 0.8),  # tau_p
        (0.01, 1.0),  # floor
        (0.5, 2.5),   # eta
    ]

    starts = [
        [1.5, 0.8, 0.8, 1.2, 1.0, 0.8, 0.4, 0.8, 0.2, 0.4, 0.2, 0.2, 0.15, 0.30, 0.35, 0.05, 0.08, 1.0],
        [2.0, 1.2, 0.6, 1.0, 1.3, 0.6, 0.3, 1.2, 0.1, 0.6, 0.4, 0.3, 0.20, 0.40, 0.45, 0.10, 0.12, 1.1],
        [1.2, 0.5, 1.1, 0.9, 0.8, 1.2, 0.6, 0.6, 0.3, 0.8, 0.1, 0.4, 0.10, 0.25, 0.30, -0.05, 0.06, 0.9],
        [2.5, 1.5, 0.7, 1.4, 0.9, 0.9, 0.8, 1.0, 0.4, 0.5, 0.3, 0.5, 0.25, 0.50, 0.50, 0.15, 0.10, 1.2],
    ]

    best = None
    best_obj = None
    for start in starts:
        result = minimize(
            objective,
            x0=np.array(start, dtype=float),
            args=(df, group_slices, team24_idx),
            bounds=bounds,
            method="L-BFGS-B",
            options={"maxiter": 2000},
        )
        obj = objective(result.x, df, group_slices, team24_idx)
        if best is None or obj < best_obj:
            best = result
            best_obj = obj

    return best


def metrics(df, eval_out):
    target = df["share_target"].to_numpy(dtype=float)
    pred = eval_out["predicted_share"]
    err = pred - target

    share_mae = float(np.mean(np.abs(err)))
    share_rmse = float(np.sqrt(np.mean(err ** 2)))
    log_share_rmse = float(np.sqrt(np.mean((np.log(pred + 1e-6) - np.log(target + 1e-6)) ** 2)))

    team24_mask = (df["team"] == TEAM_ID).to_numpy()
    team24_share_mae = float(np.mean(np.abs(err[team24_mask])))

    team24_cpi_actual = df.loc[team24_mask, "team24_actual_cpi"].to_numpy(dtype=float)
    team24_cpi_pred = eval_out["estimated_cpi"][team24_mask]
    cpi_mae = float(np.mean(np.abs(team24_cpi_pred - team24_cpi_actual)))
    cpi_rmse = float(np.sqrt(np.mean((team24_cpi_pred - team24_cpi_actual) ** 2)))

    spread_series = []
    for _, group in pd.DataFrame(
        {
            "round": df["round"],
            "market": df["market"],
            "estimated_cpi": eval_out["estimated_cpi"],
            "predicted_share": eval_out["predicted_share"],
            "share_target": target,
        }
    ).groupby(["round", "market"], sort=False):
        est = group["estimated_cpi"].to_numpy(dtype=float)
        spread_series.append(float(np.max(est) / max(np.min(est[est > 0]), 1e-9)))

    group_totals = pd.DataFrame({
        "round": df["round"],
        "market": df["market"],
        "predicted_share": eval_out["predicted_share"],
        "share_target": target,
    }).groupby(["round", "market"], sort=False).sum(numeric_only=True)
    total_mae = float(np.mean(np.abs(group_totals["predicted_share"] - group_totals["share_target"])))

    return {
        "share_mae": share_mae,
        "share_rmse": share_rmse,
        "log_share_rmse": log_share_rmse,
        "group_total_share_mae": total_mae,
        "team24_share_mae": team24_share_mae,
        "team24_cpi_mae": cpi_mae,
        "team24_cpi_rmse": cpi_rmse,
        "median_group_cpi_ratio": float(np.median(spread_series)),
        "max_group_cpi_ratio": float(np.max(spread_series)),
    }


def export(df, samples, result):
    eval_out = evaluate(df, *build_model_data(df)[2:], result.x)
    metric_values = metrics(df, eval_out)
    p = unpack_params(result.x)

    out = df.copy()
    out["predicted_share"] = eval_out["predicted_share"]
    out["estimated_cpi"] = eval_out["estimated_cpi"]
    out["raw_score"] = eval_out["raw_score"]
    out["share_basis"] = eval_out["share_basis"]
    out["predicted_share_norm"] = eval_out["predicted_share_norm"]
    out["group_scale"] = eval_out["group_scale"]
    out["group_target_total"] = eval_out["group_target_total"]
    out["group_pred_total"] = eval_out["group_pred_total"]
    out["term_share_sqrt"] = eval_out["term_share_sqrt"]
    out["term_share"] = eval_out["term_share"]
    out["m_thr"] = eval_out["m_thr"]
    out["q_thr"] = eval_out["q_thr"]
    out["mi_thr"] = eval_out["mi_thr"]
    out["p_thr"] = eval_out["p_thr"]
    out["term_m"] = eval_out["term_m"]
    out["term_q"] = eval_out["term_q"]
    out["term_mi"] = eval_out["term_mi"]
    out["term_p"] = eval_out["term_p"]
    out["term_mq"] = eval_out["term_mq"]
    out["term_qmi"] = eval_out["term_qmi"]
    out["term_brand"] = eval_out["term_brand"]
    out["term_home"] = eval_out["term_home"]
    out["estimated_rank_in_city"] = out.groupby(["round", "market"])["estimated_cpi"].rank(method="first", ascending=False).astype(int)
    out["target_share_rank"] = out.groupby(["round", "market"])["share_target"].rank(method="min", ascending=False)
    out["predicted_share_rank"] = out.groupby(["round", "market"])["predicted_share"].rank(method="min", ascending=False)
    out["is_team24"] = out["team"] == TEAM_ID

    team24_fit = (
        samples[["round", "market", "actual_competitiveness"]]
        .merge(
            out[out["team"] == TEAM_ID][
                ["round", "market", "estimated_cpi", "share_target", "predicted_share", "raw_score", "group_scale"]
            ],
            on=["round", "market"],
            how="left",
        )
        .sort_values(["round", "market"], key=lambda s: s.map(round_sort_key) if s.name == "round" else s)
        .reset_index(drop=True)
    )
    team24_fit["cpi_error"] = team24_fit["estimated_cpi"] - team24_fit["actual_competitiveness"]

    metrics_df = pd.DataFrame(
        [{
            "model": "global_constrained_threshold_share_anchor",
            "objective": float(objective(result.x, df, *build_model_data(df)[2:])),
            **metric_values,
            **p,
            "success": bool(result.success),
            "message": result.message,
        }]
    )

    params_df = pd.DataFrame({"parameter": list(p.keys()), "value": list(p.values())})

    main_cols = [
        "round", "market", "team", "estimated_rank_in_city", "estimated_cpi", "share_target", "predicted_share",
        "predicted_share_norm", "target_share_rank", "predicted_share_rank", "market_share", "market_share_clean",
        "abs_share_from_market_size", "sales_share_raw", "sales_volume",
        "management_index", "quality_index", "agents", "marketing_investment", "market_index", "price",
        "avg_price", "raw_score", "share_basis", "group_scale", "group_target_total", "group_pred_total",
        "share_target_source", "round_original", "source_file", "is_team24",
    ]
    term_cols = [
        "round", "market", "team", "estimated_cpi", "raw_score", "share_basis",
        "term_share_sqrt", "term_share",
        "m_thr", "q_thr", "mi_thr", "p_thr",
        "term_m", "term_q", "term_mi", "term_p", "term_mq", "term_qmi", "term_brand", "term_home",
    ]

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        params_df.to_excel(writer, sheet_name="parameters", index=False)
        team24_fit.to_excel(writer, sheet_name="team24_fit", index=False)
        out[term_cols].to_excel(writer, sheet_name="term_breakdown", index=False)

    with pd.ExcelWriter(PEER_XLSX, engine="openpyxl") as writer:
        out[main_cols].to_excel(writer, sheet_name="all_teams_r1_r6", index=False)
        out[out["team"] != TEAM_ID][main_cols].to_excel(writer, sheet_name="other_teams_only", index=False)
        out[term_cols].to_excel(writer, sheet_name="term_breakdown", index=False)
        for round_name in ["r1", "r2", "r3", "r4", "r5", "r6"]:
            out[out["round"] == round_name][main_cols].to_excel(writer, sheet_name=round_name, index=False)

    lines = [
        "# Global Constrained CPI Model",
        "",
        "This model fits all companies' normalized sales-share targets, then anchors each city-round CPI scale to Team 24 actual CPI.",
        "",
        "## Metrics",
    ]
    for key, value in metrics_df.iloc[0].items():
        if isinstance(value, (float, np.floating)):
            lines.append(f"- {key}: {value:.8f}")
        else:
            lines.append(f"- {key}: {value}")
    lines.extend([
        "",
        "## Notes",
        "- share_target uses normalized sales volume when available; otherwise falls back to normalized reported market share.",
        "- Team 24 CPI is anchored exactly within each city-round group.",
        f"- Peer export: {PEER_XLSX}",
    ])
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")

    return metrics_df, team24_fit, out


def main():
    teams, samples = prepare_data()
    df, _, group_slices, team24_idx = build_model_data(teams)
    result = fit_model(df, group_slices, team24_idx)
    metrics_df, team24_fit, out = export(df, samples, result)

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {PEER_XLSX}")
    print(metrics_df.to_string(index=False))
    print(team24_fit.to_string(index=False))
    print(
        out[["round", "market", "team", "estimated_cpi", "share_target", "predicted_share"]]
        .head(20)
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
