#!/usr/bin/env python3
from itertools import product

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from analyze_team24_competitiveness import round_sort_key
from fit_universal_cpi_model import build_eval_context, evaluate_prediction, prepare_universal_table, score_result
from fit_weighted_theoretical_cpi_model import BASE_DIR, EPS, base_features, build_context


OUTPUT_XLSX = BASE_DIR / "universal_no_management_cpi_model.xlsx"


def build_no_management_feature_matrix(feats, context):
    keep_context = [
        c
        for c in context.columns
        if not any(
            key in c
            for key in [
                "m_log_base",
                "m_prev_log",
                "m_vs_median_log",
                "m_rank_pct",
                "m_share",
                "management_market_synergy",
            ]
        )
    ]

    X = context[keep_context].copy()
    X["q_raw"] = feats["q_raw"]
    X["mi_raw"] = feats["mi_raw"]
    X["q_prev_raw"] = feats["q_prev_raw"]
    X["q_k"] = feats["q_k"]
    X["mi_m"] = feats["mi_m"]
    X["q_prev_k"] = feats["q_prev_k"]
    X["price"] = feats["price"].fillna(0).clip(lower=0)
    X["avg_price_clean"] = feats["avg_price_clean"].fillna(0).clip(lower=0)
    X["market_size"] = feats["market_size"].fillna(0).clip(lower=0)
    X["agents"] = feats["agents"].fillna(0).clip(lower=0)
    X["marketing_investment"] = feats["marketing_investment"].fillna(0).clip(lower=0)
    X["market_utilization_clean"] = feats["market_utilization_clean"].fillna(0).clip(lower=0)
    X["price_discount"] = feats["price_discount"]
    X["price_vs_median_log"] = feats["price_vs_median_log"]
    X["q_vs_median_log"] = feats["q_vs_median_log"]
    X["mi_vs_median_log"] = feats["mi_vs_median_log"]
    X["marketing_vs_median_log"] = feats["marketing_vs_median_log"]
    X["agents_vs_median_log"] = feats["agents_vs_median_log"]
    X["price_rank_pct"] = feats["price_rank_pct"]
    X["q_rank_pct"] = feats["q_rank_pct"]
    X["mi_rank_pct"] = feats["mi_rank_pct"]
    X["marketing_rank_pct"] = feats["marketing_rank_pct"]
    X["agents_rank_pct"] = feats["agents_rank_pct"]
    X["q_share"] = feats["q_share"]
    X["mi_share"] = feats["mi_share"]
    X["marketing_share"] = feats["marketing_share"]
    X["agents_share"] = feats["agents_share"]
    X["market_gate_power"] = feats["market_gate_power"]
    X["quality_price_synergy"] = feats["quality_price_synergy"]
    X["quality_market_synergy"] = feats["quality_market_synergy"]
    X["num_teams_market"] = feats["num_teams_market"]

    X["decision_power_no_m"] = feats["q_share"] + feats["mi_share"] + feats["price_rank_pct"]
    X["brand_x_q_prev"] = feats["brand_log"] * feats["q_prev_log"]
    X["brand_x_mi"] = feats["brand_log"] * feats["mi_log"]
    X["q_prev_x_mi"] = feats["q_prev_log"] * feats["mi_log"]
    X["price_x_quality"] = feats["price_vs_median_log"] * feats["q_vs_median_log"]
    X["price_x_market"] = feats["price_vs_median_log"] * feats["mi_vs_median_log"]
    X["quality_x_market"] = feats["q_vs_median_log"] * feats["mi_vs_median_log"]
    X["quality_rank_x_price_rank"] = feats["q_rank_pct"] * feats["price_rank_pct"]
    X["market_rank_x_price_rank"] = feats["mi_rank_pct"] * feats["price_rank_pct"]
    X["mi_share_x_q_rank"] = feats["mi_share"] * feats["q_rank_pct"]
    X["mi_share_x_price_rank"] = feats["mi_share"] * feats["price_rank_pct"]
    return X.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def search_best_model(df):
    feats = base_features(df)
    round_levels = sorted(df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_no_management_feature_matrix(feats, context)

    labeled_mask = df["is_labeled"].to_numpy(dtype=bool)
    y_log = np.log(np.maximum(df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    sample_anchor = df.loc[labeled_mask, "is_anchor_real"].to_numpy(dtype=bool)
    eval_ctx = build_eval_context(df)

    rows = []
    best = None

    grid = product(
        [200.0, 500.0, 1000.0],
        [500],
        [0.03, 0.05],
        [4, 5],
        [1],
        [0.7, 1.0],
    )
    for idx, (tw, n_estimators, lr, depth, leaf, subsample) in enumerate(grid, start=1):
        print(f"[search:no-m] candidate {idx}: tw={tw}, n_estimators={n_estimators}, lr={lr}, depth={depth}, leaf={leaf}, subsample={subsample}")
        sample_weights = np.where(sample_anchor, tw, 1.0)
        est = GradientBoostingRegressor(
            loss="squared_error",
            n_estimators=n_estimators,
            learning_rate=lr,
            max_depth=depth,
            min_samples_leaf=leaf,
            subsample=subsample,
            random_state=42,
        )
        est.fit(X.loc[labeled_mask], y_log, sample_weight=sample_weights)
        pred = np.maximum(np.exp(est.predict(X)) - EPS, 0.0)
        eval_result = evaluate_prediction(pred, eval_ctx)
        score = score_result(eval_result)
        row = {
            "model_family": "gradient_boosting_no_management_tree",
            "params": f"anchor_w={tw}, n_estimators={n_estimators}, lr={lr}, max_depth={depth}, leaf={leaf}, subsample={subsample}",
            "score": score,
            "wyef_team24_r2": eval_result["wyef_team24"]["r2"],
            "wyef_others_r2": eval_result["wyef_others"]["r2"],
            "obos_team9_r2": eval_result["obos_team9"]["r2"],
            "obos_others_r2": eval_result["obos_others"]["r2"],
            "all_real_r2": eval_result["all_real"]["r2"],
            "all_labeled_r2": eval_result["all_labeled"]["r2"],
            "wyef_team24_rmse": eval_result["wyef_team24"]["rmse"],
            "wyef_others_rmse": eval_result["wyef_others"]["rmse"],
            "obos_team9_rmse": eval_result["obos_team9"]["rmse"],
            "obos_others_rmse": eval_result["obos_others"]["rmse"],
            "all_real_rmse": eval_result["all_real"]["rmse"],
            "max_pred": eval_result["max_pred"],
        }
        rows.append(row)

        candidate = {
            "name": row["model_family"],
            "params": row["params"],
            "score": score,
            "pred": pred,
            "estimator": est,
            "columns": X.columns.tolist(),
            "round_levels": round_levels,
            "city_levels": city_levels,
            "feature_importance": getattr(est, "feature_importances_", None),
            **eval_result,
        }
        if best is None or score < best["score"]:
            best = candidate

    results = pd.DataFrame(rows).sort_values(
        ["all_real_r2", "wyef_team24_r2", "obos_team9_r2", "obos_others_r2", "score"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    return best, results


def reorder_prediction_columns(df):
    preferred = [
        "competition",
        "round",
        "market",
        "team",
        "predicted_theoretical_cpi",
        "marketshare_clean",
        "actual_real_cpi",
        "actual_real_cpi_source",
        "fit_target",
        "prediction_minus_fit_target",
        "prediction_minus_real_cpi",
        "quality_index",
        "market_index",
        "price",
        "sales_volume",
        "market_size",
        "target_source",
    ]
    remaining = [c for c in df.columns if c not in preferred]
    return df[[c for c in preferred if c in df.columns] + remaining]


def main():
    df = prepare_universal_table()
    model, results = search_best_model(df)

    pred = np.asarray(model["pred"], dtype=float)
    scored = df.copy()
    scored["predicted_theoretical_cpi"] = pred
    scored["prediction_minus_fit_target"] = scored["predicted_theoretical_cpi"] - scored["fit_target"]
    scored["prediction_minus_real_cpi"] = scored["predicted_theoretical_cpi"] - scored["actual_real_cpi"]
    scored["theoretical_cpi_rank"] = scored.groupby(["competition", "round", "market"])["predicted_theoretical_cpi"].rank(
        method="min",
        ascending=False,
    )
    scored = reorder_prediction_columns(scored)

    best_df = pd.DataFrame(
        [
            {
                "selected_model_family": model["name"],
                "selected_params": model["params"],
                "score": model["score"],
                "wyef_team24_r2": model["wyef_team24"]["r2"],
                "wyef_team24_rmse": model["wyef_team24"]["rmse"],
                "wyef_others_r2": model["wyef_others"]["r2"],
                "wyef_others_rmse": model["wyef_others"]["rmse"],
                "obos_team9_r2": model["obos_team9"]["r2"],
                "obos_team9_rmse": model["obos_team9"]["rmse"],
                "obos_others_r2": model["obos_others"]["r2"],
                "obos_others_rmse": model["obos_others"]["rmse"],
                "all_real_r2": model["all_real"]["r2"],
                "all_real_rmse": model["all_real"]["rmse"],
                "all_labeled_r2": model["all_labeled"]["r2"],
                "all_labeled_rmse": model["all_labeled"]["rmse"],
                "max_pred": model["max_pred"],
                "note": "retrained universal model with all management-related features removed",
            }
        ]
    )

    feature_importance_df = pd.DataFrame(
        {
            "feature": model["columns"],
            "importance": model["feature_importance"],
        }
    ).sort_values("importance", ascending=False).reset_index(drop=True)

    obos_only = scored[scored["competition"] == "OBOS"].copy()
    wyef_only = scored[scored["competition"] == "WYEF"].copy()

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        best_df.to_excel(writer, sheet_name="best_model", index=False)
        results.to_excel(writer, sheet_name="all_results", index=False)
        feature_importance_df.to_excel(writer, sheet_name="feature_importance", index=False)
        wyef_only.to_excel(writer, sheet_name="wyef_predictions", index=False)
        obos_only.to_excel(writer, sheet_name="obos_predictions", index=False)
        obos_only[obos_only["actual_real_cpi"].notna()].to_excel(writer, sheet_name="obos_real_only", index=False)
        scored[scored["actual_real_cpi"].notna()].to_excel(writer, sheet_name="all_real_only", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(best_df.to_string(index=False))


if __name__ == "__main__":
    main()
