#!/usr/bin/env python3
import os
from itertools import product

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, GradientBoostingRegressor, HistGradientBoostingRegressor

from analyze_team24_competitiveness import round_sort_key
from evaluate_obos_r1_r4_against_models import load_obos_validation_table
from fit_obos_only_and_evaluate_exschool import (
    TEAM_ID as EXSCHOOL_TEAM_ID,
    attach_lags,
    parse_market_report_workbooks,
    parse_team13_actual,
)
from fit_weighted_theoretical_cpi_model import (
    BASE_DIR,
    EPS,
    base_features,
    build_context,
    build_tree_feature_matrix,
    clean_market_table,
    metrics,
)


OUTPUT_XLSX = BASE_DIR / "obos_exschool_joint_universal_model.xlsx"
FAST_MODE = os.environ.get("FAST", "").strip() in {"1", "true", "TRUE", "yes", "YES"}


def prepare_obos_table():
    df = load_obos_validation_table().copy()
    df["competition"] = "OBOS"
    df["fit_target"] = np.where(
        df["is_real_cpi_row"].fillna(False),
        df["actual_cpi"],
        df["marketshare_clean"],
    )
    df["target_source"] = np.where(
        df["is_real_cpi_row"].fillna(False),
        "team9_real_cpi",
        "marketshare_clean_proxy",
    )
    df["is_labeled"] = df["fit_target"].notna()
    df["is_anchor_real"] = df["is_real_cpi_row"].fillna(False)
    df["actual_real_cpi"] = df["actual_cpi"]
    return df


def prepare_exschool_table():
    actual_df = parse_team13_actual().copy()
    market_df = parse_market_report_workbooks().copy()
    merged = market_df.merge(actual_df, on=["round", "market", "team"], how="left")
    merged = attach_lags(merged)
    merged = clean_market_table(merged)
    merged["competition"] = "EXSCHOOL"
    merged["fit_target"] = np.where(
        merged["actual_real_cpi"].notna(),
        merged["actual_real_cpi"],
        merged["marketshare_clean"],
    )
    merged["target_source"] = np.where(
        merged["actual_real_cpi"].notna(),
        "team13_real_cpi",
        "marketshare_clean_proxy",
    )
    merged["is_labeled"] = merged["fit_target"].notna()
    merged["is_anchor_real"] = merged["actual_real_cpi"].notna()

    available_keys = set(zip(merged["round"], merged["market"], merged["team"]))
    missing_rows = []
    for _, row in actual_df.iterrows():
        key = (row["round"], row["market"], row["team"])
        if key not in available_keys:
            missing_rows.append(
                {
                    "round": row["round"],
                    "market": row["market"],
                    "team": row["team"],
                    "actual_real_cpi": row["actual_real_cpi"],
                    "actual_real_cpi_source": row["actual_real_cpi_source"],
                    "competition": "EXSCHOOL",
                    "missing_market_report": True,
                }
            )
    missing_df = pd.DataFrame(missing_rows)
    merged["missing_market_report"] = False
    return merged, missing_df


def prepare_joint_table():
    obos = prepare_obos_table()
    exschool, exschool_missing = prepare_exschool_table()

    columns = sorted(set(obos.columns) | set(exschool.columns))
    combined = pd.concat(
        [obos.reindex(columns=columns), exschool.reindex(columns=columns)],
        ignore_index=True,
        sort=False,
    )
    combined["round_order"] = combined["round"].map(round_sort_key)
    combined = combined.sort_values(["competition", "round_order", "market", "team"]).reset_index(drop=True)
    return combined, exschool_missing


def add_competition_norm_features(X, df, feats):
    out = X.copy()
    out["competition_exschool"] = (df["competition"] == "EXSCHOOL").astype(float)

    raw_specs = [
        ("management_index", "m"),
        ("quality_index", "q"),
        ("market_index", "mi"),
        ("marketing_investment", "marketing"),
        ("price", "price"),
        ("sales_volume", "sales"),
        ("market_size", "market_size"),
        ("avg_price_clean", "avg_price"),
    ]
    for col, prefix in raw_specs:
        raw = df[col].fillna(0).clip(lower=0)
        q50 = df.groupby("competition")[col].transform(lambda s: s.fillna(0).clip(lower=0).quantile(0.50))
        q75 = df.groupby("competition")[col].transform(lambda s: s.fillna(0).clip(lower=0).quantile(0.75))
        q90 = df.groupby("competition")[col].transform(lambda s: s.fillna(0).clip(lower=0).quantile(0.90))
        q995 = df.groupby("competition")[col].transform(lambda s: s.fillna(0).clip(lower=0).quantile(0.995))
        clipped = np.minimum(raw, q995.fillna(raw))

        out[f"{prefix}_comp_q50_logratio"] = np.log1p(clipped) - np.log1p(q50.fillna(0))
        out[f"{prefix}_comp_q90_share"] = (
            clipped / q90.replace(0, np.nan)
        ).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(0.0, 10.0)
        out[f"{prefix}_comp_q75_step"] = (clipped >= q75.fillna(np.inf)).astype(float)
        out[f"{prefix}_comp_q90_step"] = (clipped >= q90.fillna(np.inf)).astype(float)

    out["m_rank_mid_gate"] = (feats["m_rank_pct"] >= 0.55).astype(float)
    out["m_rank_high_gate"] = (feats["m_rank_pct"] >= 0.80).astype(float)
    out["q_rank_mid_gate"] = (feats["q_rank_pct"] >= 0.55).astype(float)
    out["q_rank_high_gate"] = (feats["q_rank_pct"] >= 0.80).astype(float)
    out["mi_rank_mid_gate"] = (feats["mi_rank_pct"] >= 0.55).astype(float)
    out["mi_rank_high_gate"] = (feats["mi_rank_pct"] >= 0.80).astype(float)
    out["price_rank_mid_gate"] = (feats["price_rank_pct"] >= 0.55).astype(float)
    out["price_rank_high_gate"] = (feats["price_rank_pct"] >= 0.80).astype(float)
    out["brand_mid_gate"] = (feats["brand_log"] >= np.log1p(10.0)).astype(float)
    out["brand_high_gate"] = (feats["brand_log"] >= np.log1p(40.0)).astype(float)

    out["mi_after_q_mid_gate"] = feats["mi_log"] * out["q_rank_mid_gate"]
    out["mi_after_q_high_gate"] = feats["mi_log"] * out["q_rank_high_gate"]
    out["mi_after_m_high_gate"] = feats["mi_log"] * out["m_rank_high_gate"]
    out["price_after_q_high_gate"] = feats["p_log"] * out["q_rank_high_gate"]
    out["price_after_mi_high_gate"] = feats["p_log"] * out["mi_rank_high_gate"]
    out["brand_after_q_high_gate"] = feats["brand_log"] * out["q_rank_high_gate"]
    out["q_x_mi_high_gate"] = out["q_rank_high_gate"] * out["mi_rank_high_gate"]
    out["m_x_q_high_gate"] = out["m_rank_high_gate"] * out["q_rank_high_gate"]
    out["all_high_gate"] = out["m_rank_high_gate"] * out["q_rank_high_gate"] * out["mi_rank_high_gate"]
    out["market_gate_power_x_brand"] = feats["market_gate_power"] * feats["brand_log"]
    out["quality_market_synergy_x_brand"] = feats["quality_market_synergy"] * feats["brand_log"]
    return out.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def build_joint_feature_table(df):
    feats = base_features(df)
    round_levels = sorted(df["round"].dropna().unique(), key=round_sort_key)
    city_levels = sorted(df["market"].dropna().unique())
    context = build_context(feats, round_levels, city_levels)
    X = build_tree_feature_matrix(feats, context)
    X = add_competition_norm_features(X, df, feats)
    return X, round_levels, city_levels


def feature_sets(X):
    cols = X.columns.tolist()
    relative_cols = [
        c
        for c in cols
        if any(
            key in c
            for key in [
                "_rank_pct",
                "_vs_median_log",
                "_share",
                "decision_power",
                "market_gate_power",
                "quality_price_synergy",
                "management_market_synergy",
                "quality_market_synergy",
                "brand_log",
                "reported_brand_log",
                "m_prev_log",
                "q_prev_log",
                "p_log_base",
                "num_teams_market",
                "_gate",
                "_comp_q50_logratio",
                "_comp_q90_share",
                "_comp_q75_step",
                "_comp_q90_step",
                "competition_exschool",
            ]
        )
    ]
    relative_no_comp = [c for c in relative_cols if c != "competition_exschool"]
    no_city_round = [c for c in cols if not c.startswith("city_") and not c.startswith("round_")]
    compact = sorted(set(relative_cols + [c for c in cols if c in ["m_raw", "q_raw", "mi_raw", "price", "marketing_investment", "agents", "market_size", "avg_price_clean"]]))
    return {
        "relative_threshold_common": relative_no_comp,
        "relative_threshold_with_comp": relative_cols,
        "threshold_compnorm_no_city_round": no_city_round,
        "compact_threshold_with_comp": compact,
    }


def eval_masks(df):
    competition = df["competition"]
    is_real = df["actual_real_cpi"].notna()
    is_proxy = (~df["is_anchor_real"].fillna(False)) & df["marketshare_clean"].notna()
    return {
        "obos_real": (competition == "OBOS") & is_real,
        "exschool_real": (competition == "EXSCHOOL") & is_real,
        "obos_proxy": (competition == "OBOS") & is_proxy,
        "exschool_proxy": (competition == "EXSCHOOL") & is_proxy,
        "all_real": is_real,
        "all_labeled": df["fit_target"].notna(),
    }


def evaluate_prediction(df, pred):
    pred = np.asarray(pred, dtype=float)
    masks = eval_masks(df)
    return {
        "obos_real": metrics(df.loc[masks["obos_real"], "actual_real_cpi"], pred[masks["obos_real"].to_numpy(dtype=bool)]),
        "exschool_real": metrics(df.loc[masks["exschool_real"], "actual_real_cpi"], pred[masks["exschool_real"].to_numpy(dtype=bool)]),
        "obos_proxy": metrics(df.loc[masks["obos_proxy"], "marketshare_clean"], pred[masks["obos_proxy"].to_numpy(dtype=bool)]),
        "exschool_proxy": metrics(df.loc[masks["exschool_proxy"], "marketshare_clean"], pred[masks["exschool_proxy"].to_numpy(dtype=bool)]),
        "all_real": metrics(df.loc[masks["all_real"], "actual_real_cpi"], pred[masks["all_real"].to_numpy(dtype=bool)]),
        "all_labeled": metrics(df.loc[masks["all_labeled"], "fit_target"], pred[masks["all_labeled"].to_numpy(dtype=bool)]),
        "max_pred": float(np.max(pred)) if len(pred) else 0.0,
    }


def score_result(result):
    pen_obos_real = max(0.95 - result["obos_real"]["r2"], 0.0)
    pen_ex_real = max(0.90 - result["exschool_real"]["r2"], 0.0)
    pen_obos_proxy = max(0.80 - result["obos_proxy"]["r2"], 0.0)
    pen_ex_proxy = max(0.65 - result["exschool_proxy"]["r2"], 0.0)
    pen_max = max(result["max_pred"] - 0.40, 0.0)
    return (
        3.0 * result["obos_real"]["rmse"]
        + 4.5 * result["exschool_real"]["rmse"]
        + 1.0 * result["obos_proxy"]["rmse"]
        + 1.0 * result["exschool_proxy"]["rmse"]
        + 0.8 * result["all_labeled"]["rmse"]
        + 10.0 * pen_obos_real ** 2
        + 12.0 * pen_ex_real ** 2
        + 3.0 * pen_obos_proxy ** 2
        + 2.0 * pen_ex_proxy ** 2
        + 4.0 * pen_max ** 2
    )


def train_predict(model_family, params, X_train, y_log, sample_weights, X_all):
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
    elif model_family == "extratrees":
        est = ExtraTreesRegressor(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            min_samples_leaf=params["min_samples_leaf"],
            max_features=params["max_features"],
            random_state=42,
            n_jobs=-1,
        )
    else:
        raise ValueError(model_family)

    est.fit(X_train, y_log, sample_weight=sample_weights)
    pred = np.maximum(np.exp(est.predict(X_all)) - EPS, 0.0)
    return est, pred


def search_joint_model(df):
    X, round_levels, city_levels = build_joint_feature_table(df)
    feature_map = feature_sets(X)

    labeled_mask = df["is_labeled"].to_numpy(dtype=bool)
    y_log = np.log(np.maximum(df.loc[labeled_mask, "fit_target"].to_numpy(dtype=float), EPS))
    anchor_mask = df.loc[labeled_mask, "is_anchor_real"].to_numpy(dtype=bool)

    rows = []
    best = None

    if FAST_MODE:
        gbt_grid = list(product([20.0, 50.0, 100.0], [250], [0.03, 0.05], [3, 4], [1], [0.7]))
        hgbt_grid = list(product([20.0, 50.0], [0.05], [3, 4], [15], [2], [0.0]))
        et_grid = list(product([20.0, 50.0, 100.0], [250], [8], [1], ["sqrt"]))
    else:
        gbt_grid = list(product([20.0, 50.0, 100.0], [250, 500], [0.03, 0.05], [3, 4], [1, 2], [0.7]))
        hgbt_grid = list(product([20.0, 50.0, 100.0], [0.03, 0.05], [3, 4], [15, 31], [2, 5], [0.0, 0.1]))
        et_grid = list(product([20.0, 50.0, 100.0], [300], [8, None], [1, 2], ["sqrt", 0.5]))

    for feature_name, feature_cols in feature_map.items():
        print(f"[joint-search] feature_set={feature_name} cols={len(feature_cols)} fast={FAST_MODE}")
        X_set = X[feature_cols].fillna(0.0)
        X_train = X_set.loc[labeled_mask]

        for anchor_w, n_estimators, lr, max_depth, min_samples_leaf, subsample in gbt_grid:
            params = {
                "anchor_w": anchor_w,
                "n_estimators": n_estimators,
                "learning_rate": lr,
                "max_depth": max_depth,
                "min_samples_leaf": min_samples_leaf,
                "subsample": subsample,
            }
            sample_weights = np.where(anchor_mask, anchor_w, 1.0)
            est, pred = train_predict("gbt", params, X_train, y_log, sample_weights, X_set)
            result = evaluate_prediction(df, pred)
            score = score_result(result)
            row = {
                "model_family": "gbt",
                "feature_set": feature_name,
                "params": str(params),
                "score": score,
                "obos_real_r2": result["obos_real"]["r2"],
                "obos_real_rmse": result["obos_real"]["rmse"],
                "exschool_real_r2": result["exschool_real"]["r2"],
                "exschool_real_rmse": result["exschool_real"]["rmse"],
                "obos_proxy_r2": result["obos_proxy"]["r2"],
                "exschool_proxy_r2": result["exschool_proxy"]["r2"],
                "all_real_r2": result["all_real"]["r2"],
                "all_labeled_r2": result["all_labeled"]["r2"],
                "max_pred": result["max_pred"],
            }
            rows.append(row)
            candidate = {
                "name": row["model_family"],
                "feature_set": feature_name,
                "params": row["params"],
                "score": score,
                "pred": pred,
                "estimator": est,
                "columns": feature_cols,
                "round_levels": round_levels,
                "city_levels": city_levels,
                "feature_importance": getattr(est, "feature_importances_", None),
                **result,
            }
            if best is None or score < best["score"]:
                best = candidate

        for anchor_w, lr, max_depth, max_leaf_nodes, min_samples_leaf, l2_regularization in hgbt_grid:
            params = {
                "anchor_w": anchor_w,
                "learning_rate": lr,
                "max_depth": max_depth,
                "max_leaf_nodes": max_leaf_nodes,
                "min_samples_leaf": min_samples_leaf,
                "l2_regularization": l2_regularization,
            }
            sample_weights = np.where(anchor_mask, anchor_w, 1.0)
            est, pred = train_predict("hgbt", params, X_train, y_log, sample_weights, X_set)
            result = evaluate_prediction(df, pred)
            score = score_result(result)
            row = {
                "model_family": "hgbt",
                "feature_set": feature_name,
                "params": str(params),
                "score": score,
                "obos_real_r2": result["obos_real"]["r2"],
                "obos_real_rmse": result["obos_real"]["rmse"],
                "exschool_real_r2": result["exschool_real"]["r2"],
                "exschool_real_rmse": result["exschool_real"]["rmse"],
                "obos_proxy_r2": result["obos_proxy"]["r2"],
                "exschool_proxy_r2": result["exschool_proxy"]["r2"],
                "all_real_r2": result["all_real"]["r2"],
                "all_labeled_r2": result["all_labeled"]["r2"],
                "max_pred": result["max_pred"],
            }
            rows.append(row)
            candidate = {
                "name": row["model_family"],
                "feature_set": feature_name,
                "params": row["params"],
                "score": score,
                "pred": pred,
                "estimator": est,
                "columns": feature_cols,
                "round_levels": round_levels,
                "city_levels": city_levels,
                "feature_importance": getattr(est, "feature_importances_", None),
                **result,
            }
            if best is None or score < best["score"]:
                best = candidate

        for anchor_w, n_estimators, max_depth, min_samples_leaf, max_features in et_grid:
            params = {
                "anchor_w": anchor_w,
                "n_estimators": n_estimators,
                "max_depth": max_depth,
                "min_samples_leaf": min_samples_leaf,
                "max_features": max_features,
            }
            sample_weights = np.where(anchor_mask, anchor_w, 1.0)
            est, pred = train_predict("extratrees", params, X_train, y_log, sample_weights, X_set)
            result = evaluate_prediction(df, pred)
            score = score_result(result)
            row = {
                "model_family": "extratrees",
                "feature_set": feature_name,
                "params": str(params),
                "score": score,
                "obos_real_r2": result["obos_real"]["r2"],
                "obos_real_rmse": result["obos_real"]["rmse"],
                "exschool_real_r2": result["exschool_real"]["r2"],
                "exschool_real_rmse": result["exschool_real"]["rmse"],
                "obos_proxy_r2": result["obos_proxy"]["r2"],
                "exschool_proxy_r2": result["exschool_proxy"]["r2"],
                "all_real_r2": result["all_real"]["r2"],
                "all_labeled_r2": result["all_labeled"]["r2"],
                "max_pred": result["max_pred"],
            }
            rows.append(row)
            candidate = {
                "name": row["model_family"],
                "feature_set": feature_name,
                "params": row["params"],
                "score": score,
                "pred": pred,
                "estimator": est,
                "columns": feature_cols,
                "round_levels": round_levels,
                "city_levels": city_levels,
                "feature_importance": getattr(est, "feature_importances_", None),
                **result,
            }
            if best is None or score < best["score"]:
                best = candidate

    results_df = pd.DataFrame(rows).sort_values(
        ["score", "exschool_real_rmse", "obos_real_rmse", "exschool_real_r2", "obos_real_r2"],
        ascending=[True, True, True, False, False],
    ).reset_index(drop=True)
    return best, results_df


def reorder_columns(df):
    preferred = [
        "competition",
        "round",
        "market",
        "team",
        "predicted_theoretical_cpi",
        "fit_target",
        "target_source",
        "actual_real_cpi",
        "marketshare_clean",
        "prediction_minus_fit_target",
        "prediction_minus_actual_real_cpi",
        "prediction_minus_marketshare_clean",
        "theoretical_cpi_rank",
        "marketshare_clean_rank",
        "rank_gap_vs_marketshare_clean",
        "management_index",
        "quality_index",
        "market_index",
        "price",
        "agents",
        "marketing_investment",
        "sales_volume",
        "market_size",
        "avg_price_clean",
        "source_file",
    ]
    remaining = [c for c in df.columns if c not in preferred]
    return df[[c for c in preferred if c in df.columns] + remaining]


def main():
    df, exschool_missing = prepare_joint_table()
    model, results_df = search_joint_model(df)

    scored = df.copy()
    scored["predicted_theoretical_cpi"] = np.asarray(model["pred"], dtype=float)
    scored["prediction_minus_fit_target"] = scored["predicted_theoretical_cpi"] - scored["fit_target"]
    scored["prediction_minus_actual_real_cpi"] = scored["predicted_theoretical_cpi"] - scored["actual_real_cpi"]
    scored["prediction_minus_marketshare_clean"] = scored["predicted_theoretical_cpi"] - scored["marketshare_clean"]
    scored["theoretical_cpi_rank"] = scored.groupby(["competition", "round", "market"])["predicted_theoretical_cpi"].rank(
        method="min",
        ascending=False,
    )
    scored["marketshare_clean_rank"] = scored.groupby(["competition", "round", "market"])["marketshare_clean"].rank(
        method="min",
        ascending=False,
    )
    scored["rank_gap_vs_marketshare_clean"] = scored["theoretical_cpi_rank"] - scored["marketshare_clean_rank"]
    scored = scored.sort_values(["competition", "round_order", "market", "team"]).reset_index(drop=True)
    scored = reorder_columns(scored)

    if not exschool_missing.empty:
        for col in scored.columns:
            if col not in exschool_missing.columns:
                exschool_missing[col] = np.nan
        exschool_missing = exschool_missing[scored.columns.tolist()]

    summary_df = pd.DataFrame(
        [
            {
                "selected_model_family": model["name"],
                "selected_feature_set": model["feature_set"],
                "selected_params": model["params"],
                "score": model["score"],
                "obos_real_r2": model["obos_real"]["r2"],
                "obos_real_rmse": model["obos_real"]["rmse"],
                "exschool_real_r2": model["exschool_real"]["r2"],
                "exschool_real_rmse": model["exschool_real"]["rmse"],
                "obos_proxy_r2": model["obos_proxy"]["r2"],
                "exschool_proxy_r2": model["exschool_proxy"]["r2"],
                "all_real_r2": model["all_real"]["r2"],
                "all_real_rmse": model["all_real"]["rmse"],
                "all_labeled_r2": model["all_labeled"]["r2"],
                "all_labeled_rmse": model["all_labeled"]["rmse"],
                "exschool_missing_actual_points": len(exschool_missing),
                "note": "One joint model fitted on OBOS + exschool together. exschool missing rows are kept separately when no market report row exists.",
            }
        ]
    )

    feature_importance = model["feature_importance"]
    if feature_importance is not None and len(feature_importance) == len(model["columns"]):
        feature_importance_df = pd.DataFrame(
            {"feature": model["columns"], "importance": feature_importance}
        ).sort_values("importance", ascending=False).reset_index(drop=True)
    else:
        feature_importance_df = pd.DataFrame(columns=["feature", "importance"])

    with pd.ExcelWriter(OUTPUT_XLSX, engine="openpyxl") as writer:
        summary_df.to_excel(writer, sheet_name="summary", index=False)
        results_df.to_excel(writer, sheet_name="model_search", index=False)
        feature_importance_df.to_excel(writer, sheet_name="feature_importance", index=False)
        scored.to_excel(writer, sheet_name="all_predictions", index=False)
        scored[scored["competition"] == "OBOS"].to_excel(writer, sheet_name="obos_predictions", index=False)
        scored[(scored["competition"] == "OBOS") & scored["actual_real_cpi"].notna()].to_excel(
            writer,
            sheet_name="obos_real_only",
            index=False,
        )
        scored[(scored["competition"] == "EXSCHOOL") & (scored["team"] == EXSCHOOL_TEAM_ID)].to_excel(
            writer,
            sheet_name="exschool_team13_available",
            index=False,
        )
        exschool_missing.to_excel(writer, sheet_name="exschool_team13_missing", index=False)

    print(f"Saved: {OUTPUT_XLSX}")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
