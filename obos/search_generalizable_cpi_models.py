#!/usr/bin/env python3
from itertools import product

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, HistGradientBoostingRegressor

from analyze_team24_competitiveness import round_sort_key
from evaluate_obos_r1_r4_against_models import OUTPUT_XLSX as OBOS_VALIDATION_XLSX
from evaluate_obos_r1_r4_against_models import load_obos_validation_table
from fit_weighted_theoretical_cpi_model import (
    EPS,
    BASE_DIR,
    prepare_model_table,
    base_features,
    build_context,
    build_tree_feature_matrix,
    build_eval_context,
    evaluate_prediction,
    metrics,
)


OUTPUT_XLSX = BASE_DIR / "generalizable_cpi_model_search.xlsx"


def feature_sets(X):
    cols = X.columns.tolist()

    relative_cols = [
        c for c in cols
        if any(key in c for key in [
            "_rank_pct", "_vs_median_log", "_share", "decision_power",
            "market_gate_power", "quality_price_synergy",
            "management_market_synergy", "quality_market_synergy",
            "brand_log", "reported_brand_log", "m_prev_log", "q_prev_log",
            "m_log_base", "q_log_base", "mi_log_base", "p_log_base",
            "price_discount", "num_teams_market", "team24_home_shanghai",
            "is_shanghai",
        ])
    ]
    invariant_cols = [c for c in cols if not c.startswith("city_")]
    no_brand_cols = [c for c in cols if "brand" not in c]
    relative_no_city = [c for c in relative_cols if not c.startswith("city_") and not c.startswith("round_")]
    compact_cols = [c for c in cols if any(key in c for key in [
        "m_raw", "q_raw", "mi_raw", "price", "agents", "marketing_investment",
        "m_prev_raw", "q_prev_raw", "m_prev_log", "q_prev_log", "brand_log",
        "price_vs_median_log", "m_vs_median_log", "q_vs_median_log",
        "mi_vs_median_log", "price_rank_pct", "m_rank_pct", "q_rank_pct",
        "mi_rank_pct", "market_size", "decision_power", "market_gate_power",
        "quality_market_synergy", "management_market_synergy",
    ])]

    return {
        "full_tree": cols,
        "invariant_no_city": invariant_cols,
        "relative_only": relative_cols,
        "relative_no_city": relative_no_city,
        "no_brand": no_brand_cols,
        "compact": compact_cols,
    }


def train_predict(model_family, params, X_train, y_log, sample_weights, X_all, X_obos):
    if model_family == "gbt":
        est = GradientBoostingRegressor(
            loss="squared_error",
            n_estimators=params["n_estimators"],
            learning_rate=params["learning_rate"],
            max_depth=params["max_depth"],
            min_samples_leaf=params["min_samples_leaf"],
            subsample=params["subsample"],
            random_state=42,
        )
        est.fit(X_train, y_log, sample_weight=sample_weights)
    elif model_family == "hgbt":
        est = HistGradientBoostingRegressor(
            loss="squared_error",
            learning_rate=params["learning_rate"],
            max_depth=params["max_depth"],
            max_leaf_nodes=params["max_leaf_nodes"],
            min_samples_leaf=params["min_samples_leaf"],
            l2_regularization=params["l2_regularization"],
            random_state=42,
        )
        est.fit(X_train, y_log, sample_weight=sample_weights)
    elif model_family == "extratrees":
        est = ExtraTreesRegressor(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            min_samples_leaf=params["min_samples_leaf"],
            max_features=params["max_features"],
            random_state=42,
            n_jobs=-1,
        )
        est.fit(X_train, y_log, sample_weight=sample_weights)
    else:
        raise ValueError(model_family)

    pred_train = np.maximum(np.exp(est.predict(X_all)) - EPS, 0.0)
    pred_obos = np.maximum(np.exp(est.predict(X_obos)) - EPS, 0.0)
    return est, pred_train, pred_obos


def main():
    train_df = prepare_model_table()
    obos_df = load_obos_validation_table()

    feats = base_features(train_df)
    round_levels = sorted(train_df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(train_df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X_full = build_tree_feature_matrix(feats, context)

    obos_feats = base_features(obos_df)
    obos_context = build_context(obos_feats, round_levels, city_levels)
    X_obos_full = build_tree_feature_matrix(obos_feats, obos_context).reindex(columns=X_full.columns, fill_value=0.0)

    labeled_mask = train_df["is_labeled"].to_numpy(dtype=bool)
    y_log = np.log(np.maximum(train_df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    team24_mask = train_df.loc[labeled_mask, "is_team24"].to_numpy(dtype=bool)

    obos_actual_mask = obos_df["is_real_cpi_row"].to_numpy(dtype=bool)
    obos_actual = obos_df.loc[obos_actual_mask, "actual_cpi"].to_numpy(dtype=float)
    eval_ctx = build_eval_context(train_df)

    rows = []
    best = None
    best_pred_train = None
    best_pred_obos = None

    feature_map = feature_sets(X_full)
    gbt_grid = list(product([100.0, 300.0, 1000.0], [300, 500], [0.03, 0.05], [3, 4], [2, 4], [0.7, 1.0]))
    hgbt_grid = list(product([100.0, 300.0], [0.03, 0.05], [3, 4], [15, 31], [5, 10], [0.0, 0.1]))
    et_grid = list(product([100.0, 300.0], [400], [8, None], [2, 5], ["sqrt", 0.5]))

    for feature_name, cols in feature_map.items():
        X_set = X_full[cols].fillna(0.0)
        X_set_labeled = X_set.loc[labeled_mask]
        X_obos_set = X_obos_full[cols].fillna(0.0)

        for tw, n_estimators, learning_rate, max_depth, min_samples_leaf, subsample in gbt_grid:
            params = {
                "tw": tw,
                "n_estimators": n_estimators,
                "learning_rate": learning_rate,
                "max_depth": max_depth,
                "min_samples_leaf": min_samples_leaf,
                "subsample": subsample,
            }
            sample_weights = np.where(team24_mask, tw, 1.0)
            _, pred_train, pred_obos = train_predict("gbt", params, X_set_labeled, y_log, sample_weights, X_set, X_obos_set)
            wyef_eval = evaluate_prediction(pred_train, eval_ctx)
            obos_m = metrics(obos_actual, pred_obos[obos_actual_mask])
            row = {
                "model_family": "gbt",
                "feature_set": feature_name,
                "params": str(params),
                "obos_r2": obos_m["r2"],
                "obos_rmse": obos_m["rmse"],
                "obos_mae": obos_m["mae"],
                "obos_corr": obos_m["corr"],
                "wyef_team24_r2": wyef_eval["team24_metrics"]["r2"],
                "wyef_others_r2": wyef_eval["others_metrics"]["r2"],
                "wyef_overall_r2": wyef_eval["overall_metrics"]["r2"],
                "wyef_group_r2": wyef_eval["group_metrics"]["r2"],
            }
            rows.append(row)
            key = (
                obos_m["r2"],
                wyef_eval["others_metrics"]["r2"],
                wyef_eval["team24_metrics"]["r2"],
                wyef_eval["overall_metrics"]["r2"],
                -obos_m["rmse"],
            )
            if best is None or key > best["key"]:
                best = {"row": row, "key": key}
                best_pred_train = pred_train
                best_pred_obos = pred_obos

        for tw, learning_rate, max_depth, max_leaf_nodes, min_samples_leaf, l2_regularization in hgbt_grid:
            params = {
                "tw": tw,
                "learning_rate": learning_rate,
                "max_depth": max_depth,
                "max_leaf_nodes": max_leaf_nodes,
                "min_samples_leaf": min_samples_leaf,
                "l2_regularization": l2_regularization,
            }
            sample_weights = np.where(team24_mask, tw, 1.0)
            _, pred_train, pred_obos = train_predict("hgbt", params, X_set_labeled, y_log, sample_weights, X_set, X_obos_set)
            wyef_eval = evaluate_prediction(pred_train, eval_ctx)
            obos_m = metrics(obos_actual, pred_obos[obos_actual_mask])
            row = {
                "model_family": "hgbt",
                "feature_set": feature_name,
                "params": str(params),
                "obos_r2": obos_m["r2"],
                "obos_rmse": obos_m["rmse"],
                "obos_mae": obos_m["mae"],
                "obos_corr": obos_m["corr"],
                "wyef_team24_r2": wyef_eval["team24_metrics"]["r2"],
                "wyef_others_r2": wyef_eval["others_metrics"]["r2"],
                "wyef_overall_r2": wyef_eval["overall_metrics"]["r2"],
                "wyef_group_r2": wyef_eval["group_metrics"]["r2"],
            }
            rows.append(row)
            key = (
                obos_m["r2"],
                wyef_eval["others_metrics"]["r2"],
                wyef_eval["team24_metrics"]["r2"],
                wyef_eval["overall_metrics"]["r2"],
                -obos_m["rmse"],
            )
            if best is None or key > best["key"]:
                best = {"row": row, "key": key}
                best_pred_train = pred_train
                best_pred_obos = pred_obos

        for tw, n_estimators, max_depth, min_samples_leaf, max_features in et_grid:
            params = {
                "tw": tw,
                "n_estimators": n_estimators,
                "max_depth": max_depth,
                "min_samples_leaf": min_samples_leaf,
                "max_features": max_features,
            }
            sample_weights = np.where(team24_mask, tw, 1.0)
            _, pred_train, pred_obos = train_predict("extratrees", params, X_set_labeled, y_log, sample_weights, X_set, X_obos_set)
            wyef_eval = evaluate_prediction(pred_train, eval_ctx)
            obos_m = metrics(obos_actual, pred_obos[obos_actual_mask])
            row = {
                "model_family": "extratrees",
                "feature_set": feature_name,
                "params": str(params),
                "obos_r2": obos_m["r2"],
                "obos_rmse": obos_m["rmse"],
                "obos_mae": obos_m["mae"],
                "obos_corr": obos_m["corr"],
                "wyef_team24_r2": wyef_eval["team24_metrics"]["r2"],
                "wyef_others_r2": wyef_eval["others_metrics"]["r2"],
                "wyef_overall_r2": wyef_eval["overall_metrics"]["r2"],
                "wyef_group_r2": wyef_eval["group_metrics"]["r2"],
            }
            rows.append(row)
            key = (
                obos_m["r2"],
                wyef_eval["others_metrics"]["r2"],
                wyef_eval["team24_metrics"]["r2"],
                wyef_eval["overall_metrics"]["r2"],
                -obos_m["rmse"],
            )
            if best is None or key > best["key"]:
                best = {"row": row, "key": key}
                best_pred_train = pred_train
                best_pred_obos = pred_obos

    results_df = pd.DataFrame(rows).sort_values(
        ["obos_r2", "wyef_others_r2", "wyef_team24_r2", "wyef_overall_r2", "obos_rmse"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)

    best_df = pd.DataFrame([best["row"]])
    obos_out = obos_df.copy()
    obos_out["predicted_theoretical_cpi"] = best_pred_obos
    obos_out["obos_fit_error"] = obos_out["predicted_theoretical_cpi"] - obos_out["actual_cpi"]
    train_out = train_df.copy()
    train_out["predicted_theoretical_cpi"] = best_pred_train

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        best_df.to_excel(writer, sheet_name="best_model", index=False)
        results_df.to_excel(writer, sheet_name="all_results", index=False)
        obos_out.to_excel(writer, sheet_name="obos_predictions", index=False)
        obos_out[obos_out["is_real_cpi_row"]].to_excel(writer, sheet_name="obos_real_only", index=False)
        train_out.to_excel(writer, sheet_name="wyef_train_predictions", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(best_df.to_string(index=False))


if __name__ == "__main__":
    main()
