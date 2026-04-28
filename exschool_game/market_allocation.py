from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_ABSORPTION_CAP_RATIO = 0.0
DEFAULT_HOME_CITY_DEMAND_BOOST = 1.5
LEGACY_HOME_CITY_DEMAND_BOOST = 2.0


def integer_allocate_by_weights(total_units: int, weights: np.ndarray) -> np.ndarray:
    if total_units <= 0 or len(weights) == 0:
        return np.zeros(len(weights), dtype=float)
    weights = np.maximum(weights.astype(float), 0.0)
    weight_sum = float(weights.sum())
    if weight_sum <= 0:
        raw = np.full(len(weights), float(total_units) / float(len(weights)), dtype=float)
    else:
        raw = weights / weight_sum * float(total_units)
    base = np.floor(raw).astype(int)
    remainder = int(total_units - int(base.sum()))
    if remainder > 0:
        fractions = raw - base
        rotation = total_units % len(weights)
        tie_priority = (np.arange(len(weights)) - rotation) % len(weights)
        order = np.lexsort((tie_priority, -fractions))
        for idx in order[:remainder]:
            base[idx] += 1
    return base.astype(float)


def apply_home_city_demand_boost(
    weights: np.ndarray,
    markets: pd.Series | np.ndarray,
    home_city: str,
    *,
    home_city_demand_boost: float = DEFAULT_HOME_CITY_DEMAND_BOOST,
) -> np.ndarray:
    adjusted = np.maximum(np.asarray(weights, dtype=float), 0.0)
    if adjusted.size == 0 or float(home_city_demand_boost) <= 1.0:
        return adjusted
    market_array = np.asarray(markets, dtype=object).astype(str)
    home_mask = market_array == str(home_city)
    if not np.any(home_mask):
        return adjusted
    boosted = adjusted.copy()
    boosted[home_mask] *= float(home_city_demand_boost)
    return boosted


def redistribute_market_gaps(market_rows: pd.DataFrame, *, cap_ratio: float | None = None) -> pd.DataFrame:
    rows = market_rows.copy()
    if cap_ratio is not None:
        rows["absorption_cap_units"] = np.floor(
            np.maximum(float(cap_ratio), 0.0) * rows["cpi_demand_units_int"].fillna(0.0).clip(lower=0.0)
        )
        rows["absorbed_extra_units"] = rows.get("absorbed_extra_units", 0.0)
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
            g_mkt = float(rows.at[g_idx, "market_index"])
            g_qual = float(rows.at[g_idx, "quality_index"])
            eligible_absorbers: list[int] = []
            for a_idx in absorber_indices:
                if a_idx == g_idx:
                    continue
                if float(rows.at[a_idx, "leftover_stock"]) <= tolerance:
                    continue
                if cap_ratio is not None:
                    remaining_cap = float(rows.at[a_idx, "absorption_cap_units"]) - float(rows.at[a_idx, "absorbed_extra_units"])
                    if remaining_cap <= tolerance:
                        continue
                beats_any = (
                    float(rows.at[a_idx, "management_index"]) > g_mgmt
                    or float(rows.at[a_idx, "market_index"]) > g_mkt
                    or float(rows.at[a_idx, "quality_index"]) > g_qual
                )
                if beats_any:
                    eligible_absorbers.append(a_idx)

            if not eligible_absorbers:
                continue

            remaining_gap = float(rows.at[g_idx, "unmet_demand"])
            active_absorbers = eligible_absorbers[:]
            while remaining_gap >= 1.0 and active_absorbers:
                weights = np.array(
                    [
                        max(float(rows.at[a_idx, "predicted_theoretical_cpi"]), 0.0)
                        for a_idx in active_absorbers
                    ],
                    dtype=float,
                )
                if float(weights.sum()) <= tolerance:
                    weights = np.ones(len(active_absorbers), dtype=float)
                transferable = np.array(
                    [max(int(np.floor(float(rows.at[a_idx, "leftover_stock"]))), 0) for a_idx in active_absorbers],
                    dtype=int,
                )
                if int(transferable.sum()) <= 0:
                    break
                requested = integer_allocate_by_weights(int(np.floor(remaining_gap)), weights).astype(int)
                transferred_this_pass = 0
                next_active: list[int] = []

                for pos, a_idx in enumerate(active_absorbers):
                    absorber_leftover = int(np.floor(float(rows.at[a_idx, "leftover_stock"])))
                    if absorber_leftover <= 0:
                        continue
                    transfer = min(absorber_leftover, int(requested[pos]))
                    if cap_ratio is not None:
                        remaining_cap = int(
                            np.floor(float(rows.at[a_idx, "absorption_cap_units"]) - float(rows.at[a_idx, "absorbed_extra_units"]))
                        )
                        transfer = min(transfer, max(remaining_cap, 0))
                    if transfer <= 0:
                        if absorber_leftover > 0:
                            next_active.append(a_idx)
                        continue
                    rows.at[a_idx, "final_sales"] += transfer
                    rows.at[a_idx, "leftover_stock"] -= transfer
                    if cap_ratio is not None:
                        rows.at[a_idx, "absorbed_extra_units"] += transfer
                    transferred_this_pass += transfer
                    if int(np.floor(float(rows.at[a_idx, "leftover_stock"]))) > 0:
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


def allocate_sales_with_gap_absorption(
    scored: pd.DataFrame,
    team_total_products: dict[str, float],
    *,
    absorption_cap_ratio: float = DEFAULT_ABSORPTION_CAP_RATIO,
    home_city_demand_boost: float = DEFAULT_HOME_CITY_DEMAND_BOOST,
) -> pd.DataFrame:
    out = scored.copy()
    out["cpi_demand_units"] = out["predicted_marketshare_unconstrained"] * out["market_size"]
    out["cpi_demand_units_int"] = np.floor(out["cpi_demand_units"].clip(lower=0.0))
    if "active_market" in out.columns:
        inactive_mask = ~out["active_market"].fillna(True)
        out.loc[inactive_mask, "cpi_demand_units"] = 0.0
        out.loc[inactive_mask, "cpi_demand_units_int"] = 0.0

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
            out.loc[team_rows.index, "stock_in_market"] = integer_allocate_by_weights(
                total_products,
                weights,
            )
        else:
            out.loc[team_rows.index, "stock_in_market"] = np.floor(team_rows["sales_volume"].fillna(0.0).clip(lower=0.0))
    if "active_market" in out.columns:
        inactive_mask = ~out["active_market"].fillna(True)
        out.loc[inactive_mask, "stock_in_market"] = 0.0

    out["stock_in_market"] = np.floor(out["stock_in_market"].clip(lower=0.0))
    out["final_sales"] = np.minimum(out["stock_in_market"], out["cpi_demand_units_int"])
    out["leftover_stock"] = (out["stock_in_market"] - out["final_sales"]).clip(lower=0.0)
    out["unmet_demand"] = (out["cpi_demand_units_int"] - out["final_sales"]).clip(lower=0.0)
    out["absorbed_extra_units"] = 0.0
    out["absorption_cap_units"] = np.floor(
        np.maximum(float(absorption_cap_ratio), 0.0) * out["cpi_demand_units_int"].fillna(0.0).clip(lower=0.0)
    )

    for market_name in out["market"].dropna().unique():
        mask = out["market"] == market_name
        updated = redistribute_market_gaps(out.loc[mask], cap_ratio=absorption_cap_ratio)
        out.loc[mask, ["final_sales", "leftover_stock", "unmet_demand", "absorbed_extra_units", "absorption_cap_units"]] = updated[
            ["final_sales", "leftover_stock", "unmet_demand", "absorbed_extra_units", "absorption_cap_units"]
        ].to_numpy()

    return out
