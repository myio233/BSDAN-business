#!/usr/bin/env python3
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from analyze_team24_competitiveness import (
    BASE_DIR,
    build_sample_table,
    compute_metrics,
    load_market_reports,
    load_summary_samples,
)


OUTPUT_XLSX = BASE_DIR / "team24_model_family_comparison.xlsx"
OUTPUT_MD = BASE_DIR / "team24_model_family_comparison.md"


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -60, 60)))


def build_groups():
    all_teams = load_market_reports()
    summary_samples = load_summary_samples()
    sample_df = build_sample_table(all_teams, summary_samples)
    sample_df = sample_df[sample_df["team24_present_in_report"]].copy().reset_index(drop=True)

    groups = []
    for _, sample in sample_df.iterrows():
        market_rows = all_teams[
            (all_teams["round"] == sample["round"]) & (all_teams["market"] == sample["market"])
        ].copy()
        groups.append((sample, market_rows))

    return all_teams, sample_df, groups


def base_features(group):
    avg_price = group["avg_price"].fillna(1)
    price = group["price"].replace(0, np.nan)

    return {
        "m": group["management_index"].fillna(0).to_numpy(dtype=float),
        "q": group["quality_index"].fillna(0).to_numpy(dtype=float),
        "mi": group["market_index"].fillna(0).to_numpy(dtype=float),
        "agents": group["agents"].fillna(0).to_numpy(dtype=float),
        "price_ratio": (avg_price / price).fillna(0).to_numpy(dtype=float),
        "team_mask": (group["team"] == "24").to_numpy(),
    }


def predict_share_from_raw(raw, team_mask):
    raw = np.asarray(raw, dtype=float)
    raw = np.where(np.isfinite(raw), raw, 0.0)
    raw = np.maximum(raw, 0.0)
    total = raw.sum()
    if total <= 0:
        return 0.0
    return float(raw[team_mask][0] / total)


def predict_share_from_score(score, team_mask):
    score = np.asarray(score, dtype=float)
    score = np.where(np.isfinite(score), score, -1e9)
    shifted = score - np.max(score)
    raw = np.exp(np.clip(shifted, -60, 60))
    total = raw.sum()
    if total <= 0:
        return 0.0
    return float(raw[team_mask][0] / total)


def model_multiplicative_power(params, group):
    a, b, c, d = params
    feats = base_features(group)
    raw = (
        (1.0 + feats["m"]) ** a
        * (1.0 + feats["q"]) ** b
        * (1.0 + feats["mi"]) ** c
        * np.maximum(feats["price_ratio"], 0.0) ** d
    )
    return predict_share_from_raw(raw, feats["team_mask"])


def model_additive_nonlinear(params, group):
    wm, a, wq, b, wmi, c, wp, d = params
    feats = base_features(group)

    xm = np.log1p(feats["m"])
    xq = np.log1p(feats["q"])
    xmi = np.log1p(feats["mi"])
    xp = np.maximum(feats["price_ratio"], 1e-9)

    raw = (
        wm * (xm**a)
        + wq * (xq**b)
        + wmi * (xmi**c)
        + wp * (xp**d)
    )
    return predict_share_from_raw(raw, feats["team_mask"])


def model_interaction_softmax(params, group):
    wm, wq, wmi, wp, wmq, wmmi, wqmi = params
    feats = base_features(group)

    xm = np.log1p(feats["m"])
    xq = np.log1p(feats["q"])
    xmi = np.log1p(feats["mi"])
    xp = np.log(np.maximum(feats["price_ratio"], 1e-9))

    score = (
        wm * xm
        + wq * xq
        + wmi * xmi
        + wp * xp
        + wmq * xm * xq
        + wmmi * xm * xmi
        + wqmi * xq * xmi
    )
    return predict_share_from_score(score, feats["team_mask"])


def model_threshold_softmax(params, group):
    wm, wq, wmi, wp, bonus, threshold, steepness = params
    feats = base_features(group)

    xm = np.log1p(feats["m"])
    xq = np.log1p(feats["q"])
    xmi = np.log1p(feats["mi"])
    xp = np.log(np.maximum(feats["price_ratio"], 1e-9))

    gate = sigmoid(steepness * (xq - threshold))
    score = wm * xm + wq * xq + wmi * xmi + wp * xp + bonus * gate * xmi
    return predict_share_from_score(score, feats["team_mask"])


MODEL_SPECS = {
    "multiplicative_power": {
        "fn": model_multiplicative_power,
        "x0": np.array([0.3, 1.0, 0.05, 1.0], dtype=float),
        "bounds": [(0, 5), (0, 5), (0, 5), (0, 5)],
        "param_names": ["mgmt_exp", "quality_exp", "market_index_exp", "price_exp"],
    },
    "additive_nonlinear": {
        "fn": model_additive_nonlinear,
        "x0": np.array([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0], dtype=float),
        "bounds": [(0, 10), (0.05, 3), (0, 10), (0.05, 3), (0, 10), (0.05, 3), (0, 10), (0.05, 3)],
        "param_names": ["w_m", "a_m", "w_q", "a_q", "w_mi", "a_mi", "w_p", "a_p"],
    },
    "interaction_softmax": {
        "fn": model_interaction_softmax,
        "x0": np.array([0.3, 1.0, 0.1, 1.0, 0.05, 0.01, 0.05], dtype=float),
        "bounds": [(-5, 5), (-5, 5), (-5, 5), (-5, 5), (-2, 2), (-2, 2), (-2, 2)],
        "param_names": ["w_m", "w_q", "w_mi", "w_p", "w_mq", "w_mmi", "w_qmi"],
    },
    "threshold_softmax": {
        "fn": model_threshold_softmax,
        "x0": np.array([0.3, 1.0, 0.1, 1.0, 0.3, 2.0, 2.0], dtype=float),
        "bounds": [(-5, 5), (-5, 5), (-5, 5), (-5, 5), (-5, 5), (0, 10), (0.1, 10)],
        "param_names": ["w_m", "w_q", "w_mi", "w_p", "bonus", "threshold", "steepness"],
    },
}


def fit_one_model(model_name, spec, groups, actual):
    def predict(params):
        return np.array([spec["fn"](params, group) for _, group in groups], dtype=float)

    def objective(params):
        pred = predict(params)
        return float(np.mean((pred - actual) ** 2))

    result = minimize(
        objective,
        x0=spec["x0"],
        bounds=spec["bounds"],
        method="L-BFGS-B",
    )

    pred = predict(result.x)
    metrics = compute_metrics(actual, pred)

    row = {
        "model": model_name,
        "success": bool(result.success),
        "objective": objective(result.x),
        **metrics,
    }
    for name, value in zip(spec["param_names"], result.x):
        row[name] = value

    return row, pred


def write_markdown(model_df, pred_df):
    best_row = model_df.sort_values("rmse").iloc[0]
    lines = [
        "# Team24 Model Family Comparison",
        "",
        "可识别并已实际拟合的模型族：",
        "- 非线性加权模型",
        "- 乘法短板模型",
        "- 带交互项的 softmax/logit 模型",
        "- 带阈值门槛的 softmax/logit 模型",
        "",
        "当前数据暂时不适合直接识别的模型：",
        "- 动态滞后模型：缺少完整轮次和足够长时间序列",
        "- 利润/成本模型：缺少单位成本、固定成本、质量投入、管理投入等显式成本数据",
        "",
        "## Model Metrics",
        "",
        model_df.to_string(index=False),
        "",
        f"## Best Model: {best_row['model']}",
        "",
        f"- RMSE: {best_row['rmse']:.6f}",
        f"- MAE: {best_row['mae']:.6f}",
        f"- R²: {best_row['r2']:.6f}",
        "",
        "## Largest Errors Of Best Model",
        "",
        pred_df[pred_df["model"] == best_row["model"]][
            ["round", "market", "actual_competitiveness", "prediction", "error"]
        ]
        .assign(abs_error=lambda df: df["error"].abs())
        .sort_values("abs_error", ascending=False)
        .head(10)
        .drop(columns=["abs_error"])
        .to_string(index=False),
    ]
    OUTPUT_MD.write_text("\n".join(lines), encoding="utf-8")


def main():
    _, sample_df, groups = build_groups()
    actual = sample_df["actual_competitiveness"].to_numpy(dtype=float)

    model_rows = []
    prediction_rows = []

    for model_name, spec in MODEL_SPECS.items():
        fit_row, pred = fit_one_model(model_name, spec, groups, actual)
        model_rows.append(fit_row)
        for idx, (_, group) in enumerate(groups):
            sample = sample_df.iloc[idx]
            prediction_rows.append(
                {
                    "model": model_name,
                    "round": sample["round"],
                    "market": sample["market"],
                    "actual_competitiveness": sample["actual_competitiveness"],
                    "prediction": pred[idx],
                    "error": pred[idx] - sample["actual_competitiveness"],
                    "team24_management_index": sample["team24_management_index"],
                    "team24_quality_index": sample["team24_quality_index"],
                    "team24_agents_report": sample["team24_agents_report"],
                    "team24_marketing_report": sample["team24_marketing_report"],
                    "team24_price_report": sample["team24_price_report"],
                    "num_teams": sample["num_teams"],
                }
            )

    model_df = pd.DataFrame(model_rows).sort_values("rmse").reset_index(drop=True)
    pred_df = pd.DataFrame(prediction_rows)

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        model_df.to_excel(writer, sheet_name="model_metrics", index=False)
        pred_df.to_excel(writer, sheet_name="predictions", index=False)
        sample_df.to_excel(writer, sheet_name="team24_samples", index=False)

    write_markdown(model_df, pred_df)

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_MD}")
    print(model_df.to_string(index=False))


if __name__ == "__main__":
    main()
