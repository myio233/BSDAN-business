#!/usr/bin/env python3
from itertools import product

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor

from analyze_team24_competitiveness import round_sort_key
from evaluate_obos_r1_r4_against_models import load_obos_validation_table
from fit_weighted_theoretical_cpi_model import (
    BASE_DIR,
    EPS,
    base_features,
    build_context,
    build_tree_feature_matrix,
    fit_surrogate_formula,
    metrics,
    prepare_model_table,
    surrogate_formula_lines,
)


OUTPUT_XLSX = BASE_DIR / "universal_cpi_model.xlsx"
OUTPUT_OBOS_XLSX = BASE_DIR / "obos_r1_r4_universal_cpi.xlsx"


def prepare_universal_table():
    wyef = prepare_model_table().copy()
    wyef["competition"] = "WYEF"
    wyef["actual_real_cpi"] = np.where(wyef["is_team24"], wyef["team24_real_cpi"], np.nan)
    wyef["actual_real_cpi_source"] = np.where(wyef["is_team24"], "WYEF_team24_real_cpi", "")

    obos = load_obos_validation_table().copy()
    obos["round_original"] = obos["round"]
    obos["team24_actual_cpi"] = np.nan
    obos["team24_real_cpi"] = np.nan
    obos["proxy_cpi_target"] = obos["marketshare_clean"]
    obos["target_source"] = "marketshare_clean_proxy"
    obos.loc[obos["is_real_cpi_row"], "proxy_cpi_target"] = obos.loc[obos["is_real_cpi_row"], "actual_cpi"]
    obos.loc[obos["is_real_cpi_row"], "target_source"] = "OBOS_team9_real_cpi"
    obos["fit_target"] = np.where(obos["proxy_cpi_target"].notna(), np.maximum(obos["proxy_cpi_target"], EPS), np.nan)
    obos["is_labeled"] = obos["fit_target"].notna()
    obos["is_team24"] = False
    obos["competition"] = "OBOS"
    obos["actual_real_cpi"] = obos["actual_cpi"]
    obos["actual_real_cpi_source"] = np.where(obos["is_real_cpi_row"], "OBOS_team9_real_cpi", "")

    combined = pd.concat([wyef, obos], ignore_index=True, sort=False)
    combined["round_order"] = combined["round"].map(round_sort_key)
    combined["is_anchor_real"] = (
        (combined["competition"].eq("WYEF") & combined["is_team24"])
        | (combined["competition"].eq("OBOS") & combined["is_real_cpi_row"].fillna(False))
    )
    return combined.sort_values(["competition", "round_order", "market", "team"]).reset_index(drop=True)


def build_eval_context(df):
    masks = {
        "wyef_team24": (df["competition"] == "WYEF") & df["is_team24"],
        "wyef_others": (df["competition"] == "WYEF") & (~df["is_team24"]),
        "obos_team9": (df["competition"] == "OBOS") & df["is_real_cpi_row"].fillna(False),
        "obos_others": (df["competition"] == "OBOS") & (~df["is_real_cpi_row"].fillna(False)),
        "all_real": df["actual_real_cpi"].notna(),
        "all_labeled": df["fit_target"].notna(),
    }
    actuals = {
        "wyef_team24": df.loc[masks["wyef_team24"], "team24_real_cpi"].to_numpy(dtype=float),
        "wyef_others": df.loc[masks["wyef_others"], "marketshare_clean"].to_numpy(dtype=float),
        "obos_team9": df.loc[masks["obos_team9"], "actual_cpi"].to_numpy(dtype=float),
        "obos_others": df.loc[masks["obos_others"], "marketshare_clean"].to_numpy(dtype=float),
        "all_real": df.loc[masks["all_real"], "actual_real_cpi"].to_numpy(dtype=float),
        "all_labeled": df.loc[masks["all_labeled"], "fit_target"].to_numpy(dtype=float),
    }
    return {"masks": masks, "actuals": actuals}


def evaluate_prediction(pred, eval_ctx):
    pred = np.asarray(pred, dtype=float)
    out = {}
    for key, mask in eval_ctx["masks"].items():
        out[key] = metrics(eval_ctx["actuals"][key], pred[mask.to_numpy(dtype=bool)])
    out["max_pred"] = float(np.max(pred)) if len(pred) else 0.0
    return out


def score_result(result):
    pen_wyef24 = max(0.95 - result["wyef_team24"]["r2"], 0.0)
    pen_wyef_others = max(0.90 - result["wyef_others"]["r2"], 0.0)
    pen_obos9 = max(0.95 - result["obos_team9"]["r2"], 0.0)
    pen_obos_others = max(0.90 - result["obos_others"]["r2"], 0.0)
    pen_max = max(result["max_pred"] - 0.40, 0.0)
    return (
        4.0 * result["wyef_team24"]["rmse"]
        + 1.5 * result["wyef_others"]["rmse"]
        + 3.0 * result["obos_team9"]["rmse"]
        + 1.5 * result["obos_others"]["rmse"]
        + 1.0 * result["all_labeled"]["rmse"]
        + 12.0 * pen_wyef24 ** 2
        + 5.0 * pen_wyef_others ** 2
        + 10.0 * pen_obos9 ** 2
        + 5.0 * pen_obos_others ** 2
        + 4.0 * pen_max ** 2
    )


def targeted_key(result):
    return (
        result["wyef_team24"]["r2"],
        result["wyef_others"]["r2"],
        result["obos_team9"]["r2"],
        result["obos_others"]["r2"],
        result["all_real"]["r2"],
        -result["all_real"]["rmse"],
    )


def search_best_model(df):
    feats = base_features(df)
    round_levels = sorted(df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_tree_feature_matrix(feats, context)

    labeled_mask = df["is_labeled"].to_numpy(dtype=bool)
    y_log = np.log(np.maximum(df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    sample_anchor = df.loc[labeled_mask, "is_anchor_real"].to_numpy(dtype=bool)
    eval_ctx = build_eval_context(df)

    rows = []
    best = None
    best_targeted = None

    gbt_grid = product(
        [200.0, 300.0, 500.0],
        [500],
        [0.03, 0.05],
        [4, 5],
        [1, 2],
        [0.7],
    )
    for idx, (tw, n_estimators, lr, depth, leaf, subsample) in enumerate(gbt_grid, start=1):
        print(f"[search] GBT candidate {idx}: tw={tw}, n_estimators={n_estimators}, lr={lr}, depth={depth}, leaf={leaf}, subsample={subsample}")
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
            "model_family": "gradient_boosting_threshold_tree",
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
        thresholds_ok = (
            eval_result["wyef_team24"]["r2"] >= 0.95
            and eval_result["wyef_others"]["r2"] >= 0.90
            and eval_result["obos_team9"]["r2"] >= 0.95
            and eval_result["obos_others"]["r2"] >= 0.90
        )
        if thresholds_ok and (best_targeted is None or targeted_key(eval_result) > targeted_key(best_targeted)):
            best_targeted = candidate

    results = pd.DataFrame(rows).sort_values(
        ["wyef_team24_r2", "wyef_others_r2", "obos_team9_r2", "obos_others_r2", "score"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    return best_targeted or best, results


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
        "management_index",
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

    surrogate = fit_surrogate_formula(df, model)
    formula_lines = surrogate_formula_lines(surrogate)
    formula_df = pd.DataFrame({"formula": formula_lines})
    coef_df = surrogate["feature_importance"].copy()

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
                "surrogate_to_model_r2": surrogate["metrics_to_model"]["r2"],
                "surrogate_to_model_rmse": surrogate["metrics_to_model"]["rmse"],
            }
        ]
    )

    obos_only = scored[scored["competition"] == "OBOS"].copy()
    wyef_only = scored[scored["competition"] == "WYEF"].copy()

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        best_df.to_excel(writer, sheet_name="best_model", index=False)
        results.to_excel(writer, sheet_name="all_results", index=False)
        formula_df.to_excel(writer, sheet_name="formula", index=False)
        coef_df.to_excel(writer, sheet_name="formula_coeffs", index=False)
        wyef_only.to_excel(writer, sheet_name="wyef_predictions", index=False)
        obos_only.to_excel(writer, sheet_name="obos_predictions", index=False)
        obos_only[obos_only["actual_real_cpi"].notna()].to_excel(writer, sheet_name="obos_real_only", index=False)
        scored[scored["actual_real_cpi"].notna()].to_excel(writer, sheet_name="all_real_only", index=False)

    with pd.ExcelWriter(OUTPUT_OBOS_XLSX, engine="openpyxl") as writer:
        obos_only.to_excel(writer, sheet_name="obos_predictions", index=False)
        obos_only[obos_only["actual_real_cpi"].notna()].to_excel(writer, sheet_name="obos_real_only", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(f"Saved: {OUTPUT_OBOS_XLSX}")
    print(best_df.to_string(index=False))


if __name__ == "__main__":
    main()
