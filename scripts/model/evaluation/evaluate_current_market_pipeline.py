#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[3]
OBOS_DIR = ROOT_DIR / "obos"
LOCAL_OBOS_SUMMARIES = [ROOT_DIR / "obos" / f"r{idx}_summary.xlsx" for idx in range(1, 5)]

if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
if str(OBOS_DIR) not in sys.path:
    sys.path.insert(0, str(OBOS_DIR))

from exschool_game.engine import (  # noqa: E402
    ACTUAL_CPI_TRAIN_WEIGHT,
    PROXY_CPI_TRAIN_WEIGHT,
    TEAM_ID,
    ExschoolSimulator,
)
from exschool_game.market_allocation import (  # noqa: E402
    DEFAULT_ABSORPTION_CAP_RATIO,
    DEFAULT_HOME_CITY_DEMAND_BOOST,
    LEGACY_HOME_CITY_DEMAND_BOOST,
    apply_home_city_demand_boost,
    allocate_sales_with_gap_absorption,
    integer_allocate_by_weights,
)
from exschool_game.modeling import (  # noqa: E402
    apply_home_city_to_frame,
    infer_team_home_cities,
    predict_share_from_cpi_model,
)
from fit_team24_semidynamic_model import attach_lagged_features  # noqa: E402
from fit_weighted_theoretical_cpi_model import (  # noqa: E402
    STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LATE_CHALLENGER_SHIFT_MULTIPLIER,
    STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_EXTRA_LOG_SLOPE,
    STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LATE_MAX_LOG_SHIFT,
    STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT,
    STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD,
    STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE,
    STAGE1_RESIDUAL_CALIBRATION_MARKET,
    STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT,
    base_features,
    build_context,
    build_tree_feature_matrix,
    clean_market_table,
    metrics,
)


MANUAL_OBOS_CPI = {
    ("r1", "Chengdu", "9"): 0.0080,
    ("r1", "Hangzhou", "9"): 0.0132,
    ("r1", "Shanghai", "9"): 0.0088,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report current Exschool market-model stage metrics on Exschool and OBOS data. "
            "Stages: CPI, CPI->unconstrained share, unconstrained share->final share, end-to-end final share."
        )
    )
    parser.add_argument(
        "--absorption-cap-ratio",
        type=float,
        default=None,
        help=(
            "Optional evaluation-only cap on extra absorbed units during gap absorption, "
            "expressed as a multiple of each row's initial CPI-demand units. "
            "Example: 0 disables absorption, 0.5 caps absorbed units at 50%% of initial demand."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT_DIR / "generated_reports" / "model_pipeline_current_baseline",
        help="Directory to write reproducible outputs (`metrics.csv`, `predictions.csv`, `summary.md`, `metadata.json`).",
    )
    parser.add_argument(
        "--home-city-demand-boost",
        type=float,
        default=DEFAULT_HOME_CITY_DEMAND_BOOST,
        help=(
            "Home-city stock-allocation weight multiplier applied only when unconstrained demand exceeds stock. "
            f"Current runtime default is {DEFAULT_HOME_CITY_DEMAND_BOOST:g}; legacy behavior was {LEGACY_HOME_CITY_DEMAND_BOOST:g}."
        ),
    )
    return parser.parse_args()


def parse_numeric(value: Any, *, percent: bool = False) -> float:
    if pd.isna(value):
        return float("nan")
    text = str(value).strip()
    if not text:
        return float("nan")
    for src, dst in {
        "¥": "",
        ",": "",
        "%": "",
        ":unselected:": "",
        "YO": "0",
        "@": "0",
        "®": "0",
        " ": "",
    }.items():
        text = text.replace(src, dst)
    filtered = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
    if filtered in {"", "-", ".", "-."}:
        return float("nan")
    try:
        number = float(filtered)
    except ValueError:
        return float("nan")
    return number / 100.0 if percent else number


def round_sort_key(round_name: str) -> int:
    if round_name == "r-1":
        return -1
    match = re.match(r"r(-?\d+)", str(round_name))
    return int(match.group(1)) if match else 999


def parse_obos_summary(path: Path) -> list[dict[str, Any]]:
    df = pd.read_excel(path, sheet_name="Market Report", header=None)
    round_name = path.stem.split("_")[0]
    rows: list[dict[str, Any]] = []
    idx = 0

    while idx < len(df):
        header = df.iloc[idx, 0]
        if isinstance(header, str) and header.startswith("Market Report - "):
            market = header.replace("Market Report - ", "").strip()
            population = parse_numeric(df.iloc[idx + 3, 0])
            penetration = parse_numeric(df.iloc[idx + 3, 1], percent=True)
            market_size = parse_numeric(df.iloc[idx + 3, 2])
            total_sales_volume = parse_numeric(df.iloc[idx + 3, 3])
            avg_price = parse_numeric(df.iloc[idx + 3, 4])

            team_header_idx = idx + 5
            cols = [str(x).strip() if pd.notna(x) else "" for x in df.iloc[team_header_idx].tolist()]
            market_share_idx = cols.index("Market Share") if "Market Share" in cols else None
            cpi_idx = cols.index("竞争力") if "竞争力" in cols else None

            row_idx = team_header_idx + 1
            while row_idx < len(df):
                team_val = df.iloc[row_idx, 0]
                if pd.isna(team_val) or not re.fullmatch(r"\d+", str(team_val).strip()):
                    break
                team = str(team_val).strip()
                actual_cpi = parse_numeric(df.iloc[row_idx, cpi_idx]) if cpi_idx is not None else float("nan")
                if pd.isna(actual_cpi):
                    actual_cpi = MANUAL_OBOS_CPI.get((round_name, market, team), float("nan"))
                market_share = (
                    parse_numeric(df.iloc[row_idx, market_share_idx], percent=True)
                    if market_share_idx is not None
                    else float("nan")
                )
                agents = parse_numeric(df.iloc[row_idx, 2])
                marketing = parse_numeric(df.iloc[row_idx, 3])
                rows.append(
                    {
                        "round": round_name,
                        "market": market,
                        "team": team,
                        "management_index": parse_numeric(df.iloc[row_idx, 1]),
                        "agents": agents,
                        "marketing_investment": marketing,
                        "quality_index": parse_numeric(df.iloc[row_idx, 4]),
                        "price": parse_numeric(df.iloc[row_idx, 5]),
                        "sales_volume": parse_numeric(df.iloc[row_idx, 6]),
                        "market_share": market_share,
                        "population": population,
                        "penetration": penetration,
                        "market_size": market_size,
                        "total_sales_volume": total_sales_volume,
                        "avg_price": avg_price,
                        "market_index": (1.0 + 0.1 * (agents or 0.0)) * (marketing or 0.0),
                        "actual_real_cpi": actual_cpi,
                        "source_file": path.name,
                    }
                )
                row_idx += 1
            idx = row_idx
        else:
            idx += 1
    return rows


def load_obos_frame() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in LOCAL_OBOS_SUMMARIES:
        rows.extend(parse_obos_summary(path))
    df = pd.DataFrame(rows)
    df = attach_lagged_features(df)
    df = clean_market_table(df)
    df["competition"] = "OBOS"
    df["round_order"] = df["round"].map(round_sort_key)
    return df.sort_values(["round_order", "market", "team"]).reset_index(drop=True)


def ensure_home_city(df: pd.DataFrame, simulator: ExschoolSimulator) -> pd.DataFrame:
    home_map = infer_team_home_cities(df)
    if str(df.get("competition", pd.Series(dtype="object")).iloc[0] if not df.empty and "competition" in df.columns else "EXSCHOOL") == "EXSCHOOL":
        home_map.update(simulator.team_home_city_map)
    return apply_home_city_to_frame(df, home_map, team_id=TEAM_ID)


def build_round_team_products(round_df: pd.DataFrame, simulator: ExschoolSimulator) -> dict[str, float]:
    competition = str(round_df["competition"].iloc[0]) if "competition" in round_df.columns and not round_df.empty else "EXSCHOOL"
    round_id = str(round_df["round"].iloc[0])
    fallback = (
        round_df.groupby("team")["sales_volume"]
        .sum()
        .fillna(0.0)
        .clip(lower=0.0)
        .to_dict()
    )
    totals = {str(team): float(value) for team, value in fallback.items()}
    for team in round_df["team"].astype(str).unique():
        fixed_key = (competition, round_id, str(team))
        if fixed_key in simulator.fixed_products_by_round_team:
            totals[str(team)] = max(
                float(simulator.fixed_products_by_round_team[fixed_key]),
                float(totals.get(str(team), 0.0) or 0.0),
            )
    return totals


def base_round_allocation(
    round_df: pd.DataFrame,
    team_total_products: dict[str, float],
    *,
    home_city_demand_boost: float,
) -> pd.DataFrame:
    out = round_df.copy()
    out["cpi_demand_units"] = out["predicted_marketshare_unconstrained"] * out["market_size"]
    out["cpi_demand_units_int"] = np.floor(out["cpi_demand_units"].clip(lower=0.0))
    out["stock_in_market"] = 0.0

    for team, team_rows in out.groupby("team"):
        total_products = int(max(round(float(team_total_products.get(str(team), 0.0) or 0.0)), 0))
        weights = team_rows["cpi_demand_units"].fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
        total_demand = float(weights.sum())
        if total_products > 0 and total_demand > float(total_products):
            home_city = str(team_rows["home_city"].iloc[0]) if "home_city" in team_rows.columns and not team_rows.empty else ""
            weights = apply_home_city_demand_boost(
                weights,
                team_rows["market"].astype(str).to_numpy(),
                home_city,
                home_city_demand_boost=home_city_demand_boost,
            )
        if total_products > 0 and float(weights.sum()) > 0:
            out.loc[team_rows.index, "stock_in_market"] = integer_allocate_by_weights(total_products, weights)
        else:
            out.loc[team_rows.index, "stock_in_market"] = np.floor(team_rows["sales_volume"].fillna(0.0).clip(lower=0.0))

    out["stock_in_market"] = np.floor(out["stock_in_market"].clip(lower=0.0))
    out["initial_sales"] = np.minimum(out["stock_in_market"], out["cpi_demand_units_int"])
    out["final_sales"] = out["initial_sales"].copy()
    out["leftover_stock"] = (out["stock_in_market"] - out["final_sales"]).clip(lower=0.0)
    out["unmet_demand"] = (out["cpi_demand_units_int"] - out["final_sales"]).clip(lower=0.0)
    out["absorbed_extra_units"] = 0.0
    return out


def redistribute_market_gaps_capped(market_rows: pd.DataFrame, cap_ratio: float) -> pd.DataFrame:
    rows = market_rows.copy()
    rows["absorption_cap_units"] = np.floor(
        np.maximum(float(cap_ratio), 0.0) * rows["cpi_demand_units_int"].fillna(0.0).clip(lower=0.0)
    )

    tolerance = 1e-9
    max_iterations = 20
    for _ in range(max_iterations):
        gap_indices = rows.index[rows["unmet_demand"] > tolerance].tolist()
        absorber_indices = rows.index[rows["leftover_stock"] > tolerance].tolist()
        if not gap_indices or not absorber_indices:
            break

        changed = False
        for g_idx in gap_indices:
            if float(rows.at[g_idx, "unmet_demand"]) <= tolerance:
                continue
            g_mgmt = float(rows.at[g_idx, "management_index"])
            g_market = float(rows.at[g_idx, "market_index"])
            g_quality = float(rows.at[g_idx, "quality_index"])

            eligible_absorbers: list[int] = []
            for a_idx in absorber_indices:
                if a_idx == g_idx:
                    continue
                absorber_leftover = float(rows.at[a_idx, "leftover_stock"])
                if absorber_leftover <= tolerance:
                    continue
                remaining_cap = float(rows.at[a_idx, "absorption_cap_units"]) - float(rows.at[a_idx, "absorbed_extra_units"])
                if remaining_cap <= tolerance:
                    continue
                beats_any = (
                    float(rows.at[a_idx, "management_index"]) > g_mgmt
                    or float(rows.at[a_idx, "market_index"]) > g_market
                    or float(rows.at[a_idx, "quality_index"]) > g_quality
                )
                if beats_any:
                    eligible_absorbers.append(a_idx)

            if not eligible_absorbers:
                continue

            remaining_gap = float(rows.at[g_idx, "unmet_demand"])
            active_absorbers = eligible_absorbers[:]
            while remaining_gap >= 1.0 and active_absorbers:
                weights = np.array(
                    [max(float(rows.at[a_idx, "predicted_theoretical_cpi"]), 0.0) for a_idx in active_absorbers],
                    dtype=float,
                )
                if float(weights.sum()) <= tolerance:
                    weights = np.ones(len(active_absorbers), dtype=float)

                transferable = np.array(
                    [
                        max(
                            int(
                                np.floor(
                                    min(
                                        float(rows.at[a_idx, "leftover_stock"]),
                                        float(rows.at[a_idx, "absorption_cap_units"]) - float(rows.at[a_idx, "absorbed_extra_units"]),
                                    )
                                )
                            ),
                            0,
                        )
                        for a_idx in active_absorbers
                    ],
                    dtype=int,
                )
                if int(transferable.sum()) <= 0:
                    break

                requested = integer_allocate_by_weights(int(np.floor(remaining_gap)), weights).astype(int)
                transferred_this_pass = 0
                next_active: list[int] = []

                for pos, a_idx in enumerate(active_absorbers):
                    transfer = min(int(transferable[pos]), int(requested[pos]))
                    if transfer <= 0:
                        if int(transferable[pos]) > 0:
                            next_active.append(a_idx)
                        continue
                    rows.at[a_idx, "final_sales"] += transfer
                    rows.at[a_idx, "leftover_stock"] -= transfer
                    rows.at[a_idx, "absorbed_extra_units"] += transfer
                    transferred_this_pass += transfer
                    if int(
                        np.floor(
                            min(
                                float(rows.at[a_idx, "leftover_stock"]),
                                float(rows.at[a_idx, "absorption_cap_units"]) - float(rows.at[a_idx, "absorbed_extra_units"]),
                            )
                        )
                    ) > 0:
                        next_active.append(a_idx)

                if transferred_this_pass <= 0:
                    break

                rows.at[g_idx, "unmet_demand"] -= transferred_this_pass
                remaining_gap = float(rows.at[g_idx, "unmet_demand"])
                active_absorbers = next_active
                changed = True

        if not changed:
            break

    rows["final_sales"] = np.floor(rows["final_sales"].clip(lower=0.0))
    rows["leftover_stock"] = np.floor(rows["leftover_stock"].clip(lower=0.0))
    rows["unmet_demand"] = np.floor(rows["unmet_demand"].clip(lower=0.0))
    return rows


def allocate_with_optional_cap(
    scored: pd.DataFrame,
    simulator: ExschoolSimulator,
    *,
    absorption_cap_ratio: float | None,
    home_city_demand_boost: float,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for _, round_df in scored.groupby("round", sort=False):
        team_total_products = build_round_team_products(round_df, simulator)
        effective_cap_ratio = DEFAULT_ABSORPTION_CAP_RATIO if absorption_cap_ratio is None else absorption_cap_ratio
        if absorption_cap_ratio is None:
            allocated = allocate_sales_with_gap_absorption(
                round_df.copy(),
                team_total_products,
                absorption_cap_ratio=effective_cap_ratio,
                home_city_demand_boost=home_city_demand_boost,
            )
        else:
            base = base_round_allocation(
                round_df.copy(),
                team_total_products,
                home_city_demand_boost=home_city_demand_boost,
            )
            for market_name in base["market"].dropna().unique():
                mask = base["market"] == market_name
                updated = redistribute_market_gaps_capped(base.loc[mask], effective_cap_ratio)
                base.loc[mask, ["final_sales", "leftover_stock", "unmet_demand", "absorbed_extra_units", "absorption_cap_units"]] = updated[
                    ["final_sales", "leftover_stock", "unmet_demand", "absorbed_extra_units", "absorption_cap_units"]
                ].to_numpy()
            allocated = base
        frames.append(allocated)

    out = pd.concat(frames, ignore_index=False).sort_index()
    out["final_marketshare"] = (
        out["final_sales"] / out["market_size"].replace(0, np.nan)
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out


def evaluate_dataset(
    simulator: ExschoolSimulator,
    df: pd.DataFrame,
    *,
    dataset_name: str,
    variant: str,
    absorption_cap_ratio: float | None,
    apply_residual_calibrator: bool,
    home_city_demand_boost: float,
) -> pd.DataFrame:
    with_home = ensure_home_city(df, simulator)
    feats = base_features(with_home)
    context = build_context(feats, simulator.round_levels, simulator.market_levels)
    X = build_tree_feature_matrix(feats, context).fillna(0.0)
    X = simulator._augment_model_matrix_with_home_city(X, with_home)

    base_pred_log = simulator.cpi_model["estimator"].predict(X.reindex(columns=simulator.cpi_model["columns"], fill_value=0.0))
    pred_log, residual_log_shift, cpi_pred = simulator._predict_runtime_cpi_from_log_predictions(
        base_pred_log,
        with_home["market"],
        with_home["price"],
        rounds=with_home["round"],
        prev_marketshare_clean=with_home["prev_marketshare_clean"],
        prev_market_utilization_clean=with_home["prev_market_utilization_clean"],
        apply_residual_calibrator=apply_residual_calibrator,
    )

    share_X = simulator._build_cpi_to_share_feature_matrix(feats, cpi_pred)
    share_pred, share_delta = predict_share_from_cpi_model(simulator.share_model, share_X, cpi_pred)

    scored = with_home.copy()
    scored["variant"] = variant
    scored["dataset"] = dataset_name
    scored["predicted_theoretical_cpi_log_pre_penalty"] = pred_log
    scored["stage1_residual_log_shift"] = residual_log_shift
    scored["predicted_theoretical_cpi"] = cpi_pred
    scored["predicted_marketshare_unconstrained"] = share_pred
    scored["predicted_units_unconstrained"] = scored["predicted_marketshare_unconstrained"] * scored["market_size"]
    scored["predicted_share_delta"] = share_delta
    scored["predicted_share_uplift"] = share_delta

    allocated = allocate_with_optional_cap(
        scored,
        simulator,
        absorption_cap_ratio=absorption_cap_ratio,
        home_city_demand_boost=home_city_demand_boost,
    )
    allocated["final_share_error"] = allocated["final_marketshare"] - allocated["marketshare_clean"]
    allocated["unconstrained_share_error"] = allocated["predicted_marketshare_unconstrained"] - allocated["marketshare_clean"]
    allocated["cpi_error"] = allocated["predicted_theoretical_cpi"] - allocated["actual_real_cpi"]
    return allocated


def metric_row(
    *,
    dataset: str,
    stage: str,
    target: str,
    actual: pd.Series,
    pred: pd.Series,
    variant: str,
) -> dict[str, Any]:
    actual_arr = pd.to_numeric(actual, errors="coerce").to_numpy(dtype=float)
    pred_arr = pd.to_numeric(pred, errors="coerce").to_numpy(dtype=float)
    mask = np.isfinite(actual_arr) & np.isfinite(pred_arr)
    actual_masked = actual_arr[mask]
    pred_masked = pred_arr[mask]
    result = metrics(actual_masked, pred_masked)
    bias = float(np.mean(pred_masked - actual_masked)) if len(actual_masked) else float("nan")
    return {
        "variant": variant,
        "dataset": dataset,
        "stage": stage,
        "target": target,
        "n": int(mask.sum()),
        "mae": result["mae"],
        "rmse": result["rmse"],
        "r2": result["r2"],
        "corr": result["corr"],
        "bias": bias,
        "actual_mean": float(np.mean(actual_masked)) if len(actual_masked) else float("nan"),
        "pred_mean": float(np.mean(pred_masked)) if len(pred_masked) else float("nan"),
    }


def build_metrics_table(scored: pd.DataFrame, *, variant: str) -> pd.DataFrame:
    allocation_actual = scored["marketshare_clean"] - scored["predicted_marketshare_unconstrained"]
    allocation_pred = scored["final_marketshare"] - scored["predicted_marketshare_unconstrained"]
    rows = [
        metric_row(
            dataset=str(scored["dataset"].iloc[0]),
            stage="cpi",
            target="actual_real_cpi",
            actual=scored["actual_real_cpi"],
            pred=scored["predicted_theoretical_cpi"],
            variant=variant,
        ),
        metric_row(
            dataset=str(scored["dataset"].iloc[0]),
            stage="cpi_to_predicted_marketshare_unconstrained",
            target="marketshare_clean",
            actual=scored["marketshare_clean"],
            pred=scored["predicted_marketshare_unconstrained"],
            variant=variant,
        ),
        metric_row(
            dataset=str(scored["dataset"].iloc[0]),
            stage="predicted_marketshare_unconstrained_to_final_share",
            target="actual_share_adjustment_from_unconstrained",
            actual=allocation_actual,
            pred=allocation_pred,
            variant=variant,
        ),
        metric_row(
            dataset=str(scored["dataset"].iloc[0]),
            stage="end_to_end_final_share",
            target="marketshare_clean",
            actual=scored["marketshare_clean"],
            pred=scored["final_marketshare"],
            variant=variant,
        ),
    ]
    return pd.DataFrame(rows)


def build_metadata(
    *,
    exschool_df: pd.DataFrame,
    obos_df: pd.DataFrame,
    absorption_cap_ratio: float | None,
    home_city_demand_boost: float,
    variants: list[str],
) -> dict[str, Any]:
    return {
        "variants": variants,
        "absorption_cap_ratio": absorption_cap_ratio,
        "home_city_demand_boost": home_city_demand_boost,
        "datasets": [
            {
                "dataset": "EXSCHOOL",
                "rows": int(len(exschool_df)),
                "rounds": sorted(str(value) for value in exschool_df["round"].dropna().unique()),
                "markets": sorted(str(value) for value in exschool_df["market"].dropna().unique()),
                "actual_cpi_rows": int(pd.to_numeric(exschool_df["actual_real_cpi"], errors="coerce").notna().sum()),
                "source_summary": (
                    "Built from Exschool simulator training data (`ExschoolSimulator._training_frame()`), "
                    "which merges Exschool market-report workbooks with Team 13 actual CPI labels and lag features."
                ),
                "source_files": [
                    "exschool/report*_market_reports*.xlsx",
                    "exschool/round_*_team13.xlsx",
                    "outputs/exschool_inferred_decisions/all_companies_numeric_decisions.xlsx",
                ],
            },
            {
                "dataset": "OBOS",
                "rows": int(len(obos_df)),
                "rounds": sorted(str(value) for value in obos_df["round"].dropna().unique()),
                "markets": sorted(str(value) for value in obos_df["market"].dropna().unique()),
                "actual_cpi_rows": int(pd.to_numeric(obos_df["actual_real_cpi"], errors="coerce").notna().sum()),
                "source_summary": (
                    "Parsed from OBOS market-report summaries and augmented with lagged features; "
                    "three missing CPI labels are filled from the script's MANUAL_OBOS_CPI table."
                ),
                "source_files": [str(path.relative_to(ROOT_DIR)) for path in LOCAL_OBOS_SUMMARIES],
                "manual_cpi_overrides": [
                    {"round": round_id, "market": market, "team": team, "actual_real_cpi": cpi}
                    for (round_id, market, team), cpi in sorted(MANUAL_OBOS_CPI.items())
                ],
            },
        ],
        "allocation_notes": [
            "CPI stage uses the simulator CPI model after the same max-price penalty applied in runtime scoring.",
            (
                "CPI training uses true CPI rows with weight "
                f"{ACTUAL_CPI_TRAIN_WEIGHT:g} and proxy market-share rows with weight {PROXY_CPI_TRAIN_WEIGHT:g}."
            ),
            (
                "Current runtime/default stage-1 CPI scoring applies a capped pre-penalty Hangzhou uplift once baseline CPI exceeds "
                f"{STAGE1_RESIDUAL_CALIBRATION_CPI_THRESHOLD:g}; base slope={STAGE1_RESIDUAL_CALIBRATION_LOG_SLOPE:g}, "
                f"base max_log_shift={STAGE1_RESIDUAL_CALIBRATION_MAX_LOG_SHIFT:g}. In late-round lag states "
                f"(prev_market_utilization_clean>={STAGE1_RESIDUAL_CALIBRATION_LATE_UTILIZATION_THRESHOLD:g}), "
                f"incumbents with prev_marketshare_clean>={STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_SHARE_THRESHOLD:g} "
                f"get an extra slope of {STAGE1_RESIDUAL_CALIBRATION_LATE_INCUMBENT_EXTRA_LOG_SLOPE:g} up to "
                f"{STAGE1_RESIDUAL_CALIBRATION_LATE_MAX_LOG_SHIFT:g}. From r{STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_ROUND_THRESHOLD} onward "
                f"that late-incumbent cap tightens to {STAGE1_RESIDUAL_CALIBRATION_ROUND_AWARE_LATE_MAX_LOG_SHIFT:g}, while challengers are damped to "
                f"{STAGE1_RESIDUAL_CALIBRATION_LATE_CHALLENGER_SHIFT_MULTIPLIER:g}x."
            ),
            "The `legacy_pre_stage1_residual_calibrator` comparison variant disables only that stage-1 residual uplift.",
            "CPI->predicted_marketshare_unconstrained uses the simulator signed share-delta model before stock allocation.",
            "predicted_marketshare_unconstrained->final_share measures the allocation-stage adjustment delta.",
            "end_to_end_final_share compares the fully allocated final share against observed market share.",
            (
                "Stage-3 stock budgets are competition-aware and use max(fixed planned products, actual round-team sales totals) "
                "so the allocator does not get a stock budget below realized historical sales."
            ),
            f"Default runtime absorption cap ratio is {DEFAULT_ABSORPTION_CAP_RATIO}.",
            (
                "Stage-3 home-city stock weighting only applies under stock shortage; "
                f"current evaluation/runtime default is {home_city_demand_boost:g}x and the legacy setting was {LEGACY_HOME_CITY_DEMAND_BOOST:g}x."
            ),
        ],
    }


def build_summary_markdown(
    metrics_df: pd.DataFrame,
    metadata: dict[str, Any],
) -> str:
    metrics_table = metrics_df.fillna("").astype(str)
    header = "| " + " | ".join(metrics_table.columns) + " |"
    separator = "| " + " | ".join(["---"] * len(metrics_table.columns)) + " |"
    rows = ["| " + " | ".join(row) + " |" for row in metrics_table.to_numpy().tolist()]
    lines = [
        "# Current Market Pipeline Baseline",
        "",
        f"- Variants: `{', '.join(metadata['variants'])}`",
        f"- Absorption cap ratio: `{metadata['absorption_cap_ratio']}`",
        "",
        "## Stage Definitions",
        "",
        "- `cpi`: predicted theoretical CPI vs actual CPI label.",
        "- `cpi_to_predicted_marketshare_unconstrained`: unconstrained share implied after the CPI and signed share-delta models.",
        "- `predicted_marketshare_unconstrained_to_final_share`: allocation-stage adjustment delta, measured as `(final_share - unconstrained_share)` vs `(actual_share - unconstrained_share)`.",
        "- `end_to_end_final_share`: fully allocated final share vs actual observed share.",
        "",
        "## Data Sources",
        "",
    ]
    for dataset in metadata["datasets"]:
        lines.append(f"### {dataset['dataset']}")
        lines.append("")
        lines.append(f"- Rows: `{dataset['rows']}`")
        lines.append(f"- Rounds: `{', '.join(dataset['rounds'])}`")
        lines.append(f"- Markets: `{', '.join(dataset['markets'])}`")
        lines.append(f"- Actual CPI rows: `{dataset['actual_cpi_rows']}`")
        lines.append(f"- Summary: {dataset['source_summary']}")
        lines.append(f"- Source files: `{', '.join(dataset['source_files'])}`")
        if dataset.get("manual_cpi_overrides"):
            lines.append(f"- Manual CPI overrides: `{json.dumps(dataset['manual_cpi_overrides'], ensure_ascii=False)}`")
        lines.append("")
    lines.extend(
        [
            "## Metrics",
            "",
            header,
            separator,
            *rows,
            "",
            "## Allocation Notes",
            "",
        ]
    )
    lines.extend(f"- {note}" for note in metadata["allocation_notes"])
    lines.append("")
    return "\n".join(lines)


def save_outputs(
    metrics_df: pd.DataFrame,
    predictions_df: pd.DataFrame,
    output_dir: Path,
    *,
    metadata: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = output_dir / "metrics.csv"
    predictions_path = output_dir / "predictions.csv"
    summary_path = output_dir / "summary.md"
    metadata_path = output_dir / "metadata.json"
    metrics_df.to_csv(metrics_path, index=False)
    predictions_df.to_csv(predictions_path, index=False)
    summary_path.write_text(build_summary_markdown(metrics_df, metadata), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved metrics: {metrics_path}")
    print(f"Saved predictions: {predictions_path}")
    print(f"Saved summary: {summary_path}")
    print(f"Saved metadata: {metadata_path}")


def main() -> None:
    args = parse_args()

    simulator = ExschoolSimulator()
    exschool_df = simulator._training_frame().copy()
    exschool_df["competition"] = "EXSCHOOL"
    obos_df = load_obos_frame()

    effective_absorption_cap_ratio = DEFAULT_ABSORPTION_CAP_RATIO if args.absorption_cap_ratio is None else args.absorption_cap_ratio
    if args.absorption_cap_ratio is None:
        variants = [
            ("current_runtime_default", True),
            ("legacy_pre_stage1_residual_calibrator", False),
        ]
    else:
        variants = [(f"cap_{args.absorption_cap_ratio:g}", False)]

    scored_frames: list[pd.DataFrame] = []
    metric_frames: list[pd.DataFrame] = []
    for variant, apply_residual_calibrator in variants:
        exschool_scored = evaluate_dataset(
            simulator,
            exschool_df,
            dataset_name="EXSCHOOL",
            variant=variant,
            absorption_cap_ratio=args.absorption_cap_ratio,
            apply_residual_calibrator=apply_residual_calibrator,
            home_city_demand_boost=args.home_city_demand_boost,
        )
        obos_scored = evaluate_dataset(
            simulator,
            obos_df,
            dataset_name="OBOS",
            variant=variant,
            absorption_cap_ratio=args.absorption_cap_ratio,
            apply_residual_calibrator=apply_residual_calibrator,
            home_city_demand_boost=args.home_city_demand_boost,
        )
        scored_frames.extend([exschool_scored, obos_scored])
        metric_frames.extend(
            [
                build_metrics_table(exschool_scored, variant=variant),
                build_metrics_table(obos_scored, variant=variant),
            ]
        )

    metrics_df = pd.concat(metric_frames, ignore_index=True)
    predictions_df = pd.concat(scored_frames, ignore_index=True, sort=False)
    display_cols = ["variant", "dataset", "stage", "target", "n", "mae", "rmse", "r2", "corr", "bias"]
    print(metrics_df[display_cols].to_string(index=False))

    if args.absorption_cap_ratio is None:
        print("\nNotes:")
        print(f"- Final-share metrics use the current runtime allocation pipeline with absorption cap ratio {DEFAULT_ABSORPTION_CAP_RATIO:g}.")
        print("- Stock budgets are competition-aware and use max(fixed planned products, actual round-team sales totals).")
        print(
            f"- Home-city stock boost under shortage is {args.home_city_demand_boost:g}x "
            f"(legacy was {LEGACY_HOME_CITY_DEMAND_BOOST:g}x)."
        )
    else:
        print("\nNotes:")
        print(f"- Final-share metrics use an evaluation-only capped absorption ratio of {args.absorption_cap_ratio:g}.")
        print("- The cap limits extra absorbed units per row to cap_ratio * initial CPI-demand units.")
        print("- CPI and unconstrained-share metrics are unchanged by the cap; only final-share metrics move.")
        print(
            f"- Home-city stock boost under shortage is {args.home_city_demand_boost:g}x "
            f"(legacy was {LEGACY_HOME_CITY_DEMAND_BOOST:g}x)."
        )

    metadata = build_metadata(
        exschool_df=exschool_df,
        obos_df=obos_df,
        absorption_cap_ratio=effective_absorption_cap_ratio,
        home_city_demand_boost=args.home_city_demand_boost,
        variants=[name for name, _ in variants],
    )
    save_outputs(metrics_df, predictions_df, args.output_dir, metadata=metadata)


if __name__ == "__main__":
    main()
