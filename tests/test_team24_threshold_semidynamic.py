#!/usr/bin/env python3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from analyze_team24_competitiveness import (
    BASE_DIR,
    build_sample_table,
    load_market_reports,
    load_summary_samples,
    round_sort_key,
)
from fit_team24_semidynamic_model import attach_lagged_features, semidynamic_prediction


OUTPUT_XLSX = BASE_DIR / "team24_threshold_semidynamic_tests.xlsx"
OUTPUT_MD = BASE_DIR / "team24_threshold_semidynamic_tests.md"
TEAM_ID = "24"
SHARPNESS = 4.0


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def compute_metrics(actual, pred):
    actual = np.asarray(actual, dtype=float)
    pred = np.asarray(pred, dtype=float)
    err = pred - actual
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(err**2)))
    ss_tot = float(np.sum((actual - actual.mean()) ** 2))
    r2 = float(1 - np.sum(err**2) / ss_tot) if ss_tot > 0 else np.nan
    rss = float(np.sum(err**2))
    return {"mae": mae, "rmse": rmse, "r2": r2, "rss": rss}


def aic_bic(actual, pred, k):
    n = len(actual)
    rss = np.sum((pred - actual) ** 2)
    rss = max(float(rss), 1e-12)
    aic = n * np.log(rss / n) + 2 * k
    bic = n * np.log(rss / n) + k * np.log(n)
    return float(aic), float(bic)


def build_dataset():
    all_teams = attach_lagged_features(load_market_reports())
    summary_samples = load_summary_samples()
    sample_df = build_sample_table(all_teams, summary_samples)
    sample_df = sample_df[sample_df["team24_present_in_report"]].copy()
    sample_df["round_order"] = sample_df["round"].map(round_sort_key)
    sample_df = sample_df.sort_values(["round_order", "market"]).reset_index(drop=True)

    groups = []
    for _, sample in sample_df.iterrows():
        group = all_teams[
            (all_teams["round"] == sample["round"]) & (all_teams["market"] == sample["market"])
        ].copy()
        groups.append(group)

    return sample_df, groups


def extract_features(group):
    m_cur = np.log1p(group["management_index"].fillna(0).to_numpy(dtype=float))
    q_cur = np.log1p(group["quality_index"].fillna(0).to_numpy(dtype=float))
    m_prev = np.log1p(group["prev_team_management_index"].fillna(0).to_numpy(dtype=float))
    q_prev = np.log1p(group["prev_team_quality_index"].fillna(0).to_numpy(dtype=float))
    brand = np.log1p(np.maximum(group["prev_market_share"].fillna(0).to_numpy(dtype=float), 0) * 1000.0)
    mi = np.log1p(group["market_index"].fillna(0).to_numpy(dtype=float))
    price_ratio = (
        group["avg_price"].fillna(1) / group["price"].replace(0, np.nan)
    ).fillna(1e-9)
    p = np.log(np.maximum(price_ratio.to_numpy(dtype=float), 1e-9))
    team_mask = (group["team"] == TEAM_ID).to_numpy()
    return {
        "m_cur": m_cur,
        "q_cur": q_cur,
        "m_prev": m_prev,
        "q_prev": q_prev,
        "brand": brand,
        "mi": mi,
        "p": p,
        "team_mask": team_mask,
    }


def softmax_share(score, team_mask):
    shifted = score - np.max(score)
    raw = np.exp(np.clip(shifted, -60, 60))
    total = raw.sum()
    if total <= 0:
        return 0.0
    return float(raw[team_mask][0] / total)


def base_score_from_params(group, base_params):
    w_m, w_q, w_mi, w_p, w_mq, w_mmi, w_miq, w_brand, rho_m, rho_q = base_params
    feats = extract_features(group)
    m_eff = feats["m_cur"] + rho_m * feats["m_prev"]
    q_eff = feats["q_cur"] + rho_q * feats["q_prev"]

    score = (
        w_m * m_eff
        + w_q * q_eff
        + w_mi * feats["mi"]
        + w_p * feats["p"]
        + w_mq * m_eff * q_eff
        + w_mmi * m_eff * feats["mi"]
        + w_miq * feats["mi"] * q_eff
        + w_brand * feats["brand"]
    )
    return score, feats, m_eff, q_eff


def threshold_term(model_name, bonus, tau, feats, m_eff, q_eff):
    if model_name == "mgmt_threshold":
        gate = sigmoid(SHARPNESS * (m_eff - tau))
        return bonus * gate * m_eff
    if model_name == "quality_threshold":
        gate = sigmoid(SHARPNESS * (q_eff - tau))
        return bonus * gate * q_eff
    if model_name == "market_threshold":
        gate = sigmoid(SHARPNESS * (feats["mi"] - tau))
        return bonus * gate * feats["mi"]
    if model_name == "price_threshold":
        gate = sigmoid(SHARPNESS * (feats["p"] - tau))
        return bonus * gate * feats["p"]
    if model_name == "quality_unlocks_market":
        gate = sigmoid(SHARPNESS * (q_eff - tau))
        return bonus * gate * feats["mi"]
    raise ValueError(model_name)


def predict_threshold_model(groups, base_params, threshold_params, model_name):
    bonus, tau = threshold_params
    preds = []
    for group in groups:
        score, feats, m_eff, q_eff = base_score_from_params(group, base_params)
        score = score + threshold_term(model_name, bonus, tau, feats, m_eff, q_eff)
        preds.append(softmax_share(score, feats["team_mask"]))
    return np.array(preds, dtype=float)


def predict_combined_threshold_model(groups, base_params, params):
    bonus_m, tau_m, bonus_q, tau_q, bonus_mi, tau_mi, bonus_p, tau_p, bonus_qmi, tau_qmi = params
    preds = []
    for group in groups:
        score, feats, m_eff, q_eff = base_score_from_params(group, base_params)
        score = (
            score
            + threshold_term("mgmt_threshold", bonus_m, tau_m, feats, m_eff, q_eff)
            + threshold_term("quality_threshold", bonus_q, tau_q, feats, m_eff, q_eff)
            + threshold_term("market_threshold", bonus_mi, tau_mi, feats, m_eff, q_eff)
            + threshold_term("price_threshold", bonus_p, tau_p, feats, m_eff, q_eff)
            + threshold_term("quality_unlocks_market", bonus_qmi, tau_qmi, feats, m_eff, q_eff)
        )
        preds.append(softmax_share(score, feats["team_mask"]))
    return np.array(preds, dtype=float)


def feature_ranges(groups, base_params):
    all_m = []
    all_q = []
    all_mi = []
    all_p = []
    for group in groups:
        _, feats, m_eff, q_eff = base_score_from_params(group, base_params)
        all_m.extend(m_eff.tolist())
        all_q.extend(q_eff.tolist())
        all_mi.extend(feats["mi"].tolist())
        all_p.extend(feats["p"].tolist())

    def bounds(vals):
        vals = np.asarray(vals, dtype=float)
        return (float(np.nanmin(vals)), float(np.nanmax(vals)), float(np.nanmedian(vals)))

    return {
        "m": bounds(all_m),
        "q": bounds(all_q),
        "mi": bounds(all_mi),
        "p": bounds(all_p),
    }


def fit_base_semidynamic(groups, actual):
    x0 = np.array([0.166767, 1.130079, -0.040338, 1.045803, 0.025702, 0.002783, 0.00121, 0.157779, 0.0, 0.0], dtype=float)
    bounds = [(-5, 5), (-5, 5), (-5, 5), (-5, 5), (-2, 2), (-2, 2), (-2, 2), (-5, 5), (0, 1), (0, 1)]

    def predict(params):
        return np.array([semidynamic_prediction(group, params) for group in groups], dtype=float)

    result = minimize(
        lambda params: np.mean((predict(params) - actual) ** 2),
        x0=x0,
        bounds=bounds,
        method="L-BFGS-B",
    )
    pred = predict(result.x)
    return result, pred


def main():
    sample_df, groups = build_dataset()
    actual = sample_df["actual_competitiveness"].to_numpy(dtype=float)

    base_result, base_pred = fit_base_semidynamic(groups, actual)
    base_params = base_result.x
    ranges = feature_ranges(groups, base_params)

    rows = []
    pred_rows = []

    base_metrics = compute_metrics(actual, base_pred)
    base_aic, base_bic = aic_bic(actual, base_pred, 10)
    rows.append(
        {
            "model": "base_semidynamic",
            "success": bool(base_result.success),
            **base_metrics,
            "aic": base_aic,
            "bic": base_bic,
        }
    )

    threshold_specs = {
        "mgmt_threshold": {"range_key": "m"},
        "quality_threshold": {"range_key": "q"},
        "market_threshold": {"range_key": "mi"},
        "price_threshold": {"range_key": "p"},
        "quality_unlocks_market": {"range_key": "q"},
    }

    all_preds = {"base_semidynamic": base_pred}

    for model_name, spec in threshold_specs.items():
        low, high, mid = ranges[spec["range_key"]]
        x0 = np.array([0.0, mid], dtype=float)
        bounds = [(-5, 5), (low, high)]

        def predict(params):
            return predict_threshold_model(groups, base_params, params, model_name)

        result = minimize(
            lambda params: np.mean((predict(params) - actual) ** 2),
            x0=x0,
            bounds=bounds,
            method="L-BFGS-B",
        )
        pred = predict(result.x)
        metrics = compute_metrics(actual, pred)
        aic, bic = aic_bic(actual, pred, 12)
        rows.append(
            {
                "model": model_name,
                "success": bool(result.success),
                **metrics,
                "aic": aic,
                "bic": bic,
                "bonus": result.x[0],
                "threshold": result.x[1],
                "threshold_feature": spec["range_key"],
                "delta_rmse_vs_base": metrics["rmse"] - base_metrics["rmse"],
                "delta_aic_vs_base": aic - base_aic,
                "delta_bic_vs_base": bic - base_bic,
            }
        )
        all_preds[model_name] = pred

    combined_x0 = np.array(
        [
            0.0, ranges["m"][2],
            0.0, ranges["q"][2],
            0.0, ranges["mi"][2],
            0.0, ranges["p"][2],
            0.0, ranges["q"][2],
        ],
        dtype=float,
    )
    combined_bounds = [
        (-5, 5), (ranges["m"][0], ranges["m"][1]),
        (-5, 5), (ranges["q"][0], ranges["q"][1]),
        (-5, 5), (ranges["mi"][0], ranges["mi"][1]),
        (-5, 5), (ranges["p"][0], ranges["p"][1]),
        (-5, 5), (ranges["q"][0], ranges["q"][1]),
    ]

    combined_result = minimize(
        lambda params: np.mean((predict_combined_threshold_model(groups, base_params, params) - actual) ** 2),
        x0=combined_x0,
        bounds=combined_bounds,
        method="L-BFGS-B",
    )
    combined_pred = predict_combined_threshold_model(groups, base_params, combined_result.x)
    combined_metrics = compute_metrics(actual, combined_pred)
    combined_aic, combined_bic = aic_bic(actual, combined_pred, 20)
    rows.append(
        {
            "model": "combined_thresholds",
            "success": bool(combined_result.success),
            **combined_metrics,
            "aic": combined_aic,
            "bic": combined_bic,
            "delta_rmse_vs_base": combined_metrics["rmse"] - base_metrics["rmse"],
            "delta_aic_vs_base": combined_aic - base_aic,
            "delta_bic_vs_base": combined_bic - base_bic,
            "bonus_m": combined_result.x[0],
            "tau_m": combined_result.x[1],
            "bonus_q": combined_result.x[2],
            "tau_q": combined_result.x[3],
            "bonus_mi": combined_result.x[4],
            "tau_mi": combined_result.x[5],
            "bonus_p": combined_result.x[6],
            "tau_p": combined_result.x[7],
            "bonus_qmi": combined_result.x[8],
            "tau_qmi": combined_result.x[9],
        }
    )
    all_preds["combined_thresholds"] = combined_pred

    metrics_df = pd.DataFrame(rows).sort_values(["rmse", "aic"]).reset_index(drop=True)

    predictions = []
    for model_name, pred in all_preds.items():
        for idx, value in enumerate(pred):
            sample = sample_df.iloc[idx]
            predictions.append(
                {
                    "model": model_name,
                    "round": sample["round"],
                    "market": sample["market"],
                    "actual_competitiveness": sample["actual_competitiveness"],
                    "prediction": value,
                    "error": value - sample["actual_competitiveness"],
                }
            )
    prediction_df = pd.DataFrame(predictions)

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        metrics_df.to_excel(writer, sheet_name="metrics", index=False)
        prediction_df.to_excel(writer, sheet_name="predictions", index=False)
        sample_df.to_excel(writer, sheet_name="samples", index=False)

    best = metrics_df.iloc[0]
    lines = [
        "# Team24 Threshold Semi-Dynamic Tests",
        "",
        "在半动态 Logit 基准上，分别单独增加以下阈值机制：",
        "- 管理指数阈值",
        "- 质量指数阈值",
        "- 市场指数阈值",
        "- 价格阈值",
        "- 质量过线后营销放大",
        "- 全部阈值同时存在",
        "",
        f"固定平滑门槛 sharpness = {SHARPNESS}",
        "",
        "## Metrics",
        "",
        metrics_df.to_string(index=False),
        "",
        f"## Best By RMSE: {best['model']}",
        "",
        f"- RMSE = {best['rmse']:.6f}",
        f"- MAE = {best['mae']:.6f}",
        f"- R² = {best['r2']:.6f}",
        f"- delta_rmse_vs_base = {best.get('delta_rmse_vs_base', 0.0):.6f}",
        f"- delta_aic_vs_base = {best.get('delta_aic_vs_base', 0.0):.6f}",
        f"- delta_bic_vs_base = {best.get('delta_bic_vs_base', 0.0):.6f}",
        "",
        "## Interpretation Rule",
        "",
        "- `delta_aic_vs_base < 0` means the added threshold is justified even after penalizing extra parameters.",
        "- `delta_bic_vs_base < 0` is stronger evidence, because BIC penalizes complexity harder.",
        "- If RMSE improves but AIC/BIC worsen, the threshold effect is probably weak or unstable.",
    ]
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(metrics_df.to_string(index=False))


if __name__ == "__main__":
    main()
