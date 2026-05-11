from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ.setdefault("EXSCHOOL_SKIP_SMART_FIXED_OPPONENT_BOOTSTRAP", "1")

from exschool_game.engine import TEAM_ID, ExschoolSimulator
from exschool_game.models import MarketDecision, SimulationInput
from exschool_game.research import patent_cost_multiplier
from exschool_game.workforce import productivity_multiplier, workforce_plan


SOURCE_DECISIONS_XLSX = ROOT_DIR / "outputs" / "exschool_inferred_decisions" / "all_companies_numeric_decisions_real_original_fixed.xlsx"
LEGACY_DECISIONS_XLSX = ROOT_DIR / "outputs" / "exschool_inferred_decisions" / "all_companies_numeric_decisions.xlsx"
SMART_DECISIONS_XLSX = ROOT_DIR / "outputs" / "exschool_inferred_decisions" / "all_companies_numeric_decisions_smart.xlsx"
REPORT_DIR = ROOT_DIR / "generated_reports" / "smart_opponents_20260422"
OUR_TEAM = str(TEAM_ID)
MARKETS = ["Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"]
ROUND_ORDER = {"r1": 1, "r2": 2, "r3": 3, "r4": 4}
CALIBRATION_HOME_CITY = "Shanghai"


ARCHETYPES: tuple[dict[str, Any], ...] = (
    {
        "name": "national_brand",
        "market_bias": {"Shanghai": 1.36, "Chengdu": 1.14, "Wuhan": 0.94, "Wuxi": 0.76, "Ningbo": 0.72},
        "price_mult": 1.00,
        "marketing_mult": 1.08,
        "talent_mult": 0.96,
        "quality_mult": 0.92,
        "mgmt_mult": 0.94,
        "worker_salary_mult": 1.00,
        "engineer_salary_mult": 1.00,
        "focus_power": {"r1": 1.32, "r2": 1.24, "r3": 1.14, "r4": 1.08},
        "loan_mult": {"r1": 0.94, "r2": 0.96, "r3": 0.98, "r4": 0.98},
        "payroll_mult": {"r1": 0.94, "r2": 0.98, "r3": 0.96, "r4": 0.92},
        "marketing_round_mult": {"r1": 1.14, "r2": 1.12, "r3": 1.00, "r4": 0.92},
        "marketing_per_agent": {"r1": 80_000.0, "r2": 105_000.0, "r3": 100_000.0, "r4": 90_000.0},
        "marketing_cash_cap": {"r1": 0.06, "r2": 0.07, "r3": 0.07, "r4": 0.06},
        "agent_total_mult": {"r1": 1.12, "r2": 1.10, "r3": 1.02, "r4": 1.00},
        "utilization_mult": {"r1": 0.92, "r2": 0.94, "r3": 0.96, "r4": 0.98},
        "price_round_mult": {"r1": 1.02, "r2": 1.01, "r3": 1.00, "r4": 0.99},
        "demand_per_agent": {"r1": 175.0, "r2": 210.0, "r3": 235.0, "r4": 245.0},
        "inventory_buffer": {"r1": 1.10, "r2": 1.12, "r3": 1.10, "r4": 1.08},
    },
    {
        "name": "balanced_scaler",
        "market_bias": {"Shanghai": 1.05, "Chengdu": 1.02, "Wuhan": 1.00, "Wuxi": 0.96, "Ningbo": 0.95},
        "price_mult": 1.00,
        "marketing_mult": 0.92,
        "talent_mult": 1.00,
        "quality_mult": 1.00,
        "mgmt_mult": 1.00,
        "worker_salary_mult": 1.00,
        "engineer_salary_mult": 1.01,
        "focus_power": {"r1": 1.02, "r2": 1.03, "r3": 1.03, "r4": 1.04},
        "loan_mult": {"r1": 1.00, "r2": 1.00, "r3": 1.00, "r4": 1.00},
        "payroll_mult": {"r1": 0.98, "r2": 1.00, "r3": 1.00, "r4": 1.00},
        "marketing_round_mult": {"r1": 0.96, "r2": 1.00, "r3": 1.02, "r4": 1.04},
        "marketing_per_agent": {"r1": 70_000.0, "r2": 85_000.0, "r3": 95_000.0, "r4": 105_000.0},
        "marketing_cash_cap": {"r1": 0.06, "r2": 0.07, "r3": 0.08, "r4": 0.08},
        "agent_total_mult": {"r1": 1.00, "r2": 1.00, "r3": 1.00, "r4": 1.00},
        "utilization_mult": {"r1": 0.94, "r2": 0.96, "r3": 0.98, "r4": 1.00},
        "price_round_mult": {"r1": 1.00, "r2": 1.00, "r3": 1.00, "r4": 1.00},
        "demand_per_agent": {"r1": 190.0, "r2": 230.0, "r3": 255.0, "r4": 280.0},
        "inventory_buffer": {"r1": 1.10, "r2": 1.10, "r3": 1.08, "r4": 1.06},
    },
    {
        "name": "east_coast",
        "market_bias": {"Shanghai": 1.18, "Chengdu": 0.72, "Wuhan": 0.74, "Wuxi": 1.34, "Ningbo": 1.42},
        "price_mult": 1.03,
        "marketing_mult": 0.78,
        "talent_mult": 0.95,
        "quality_mult": 1.45,
        "mgmt_mult": 1.08,
        "worker_salary_mult": 1.01,
        "engineer_salary_mult": 1.08,
        "focus_power": {"r1": 1.54, "r2": 1.50, "r3": 1.42, "r4": 1.36},
        "loan_mult": {"r1": 0.90, "r2": 0.92, "r3": 0.96, "r4": 0.98},
        "payroll_mult": {"r1": 0.86, "r2": 0.90, "r3": 0.96, "r4": 0.98},
        "marketing_round_mult": {"r1": 0.82, "r2": 0.88, "r3": 0.96, "r4": 1.00},
        "marketing_per_agent": {"r1": 40_000.0, "r2": 55_000.0, "r3": 70_000.0, "r4": 80_000.0},
        "marketing_cash_cap": {"r1": 0.04, "r2": 0.05, "r3": 0.06, "r4": 0.07},
        "agent_total_mult": {"r1": 0.86, "r2": 0.90, "r3": 0.96, "r4": 1.04},
        "utilization_mult": {"r1": 0.88, "r2": 0.92, "r3": 0.96, "r4": 0.98},
        "price_round_mult": {"r1": 1.03, "r2": 1.04, "r3": 1.03, "r4": 1.02},
        "demand_per_agent": {"r1": 145.0, "r2": 170.0, "r3": 205.0, "r4": 225.0},
        "inventory_buffer": {"r1": 1.05, "r2": 1.06, "r3": 1.06, "r4": 1.05},
    },
    {
        "name": "central_operator",
        "market_bias": {"Shanghai": 0.74, "Chengdu": 1.22, "Wuhan": 1.30, "Wuxi": 0.84, "Ningbo": 0.82},
        "price_mult": 0.95,
        "marketing_mult": 0.82,
        "talent_mult": 1.12,
        "quality_mult": 1.20,
        "mgmt_mult": 1.30,
        "worker_salary_mult": 1.02,
        "engineer_salary_mult": 1.06,
        "focus_power": {"r1": 1.36, "r2": 1.28, "r3": 1.20, "r4": 1.14},
        "loan_mult": {"r1": 1.00, "r2": 1.00, "r3": 0.98, "r4": 0.96},
        "payroll_mult": {"r1": 1.04, "r2": 1.06, "r3": 1.04, "r4": 1.02},
        "marketing_round_mult": {"r1": 0.84, "r2": 0.88, "r3": 0.94, "r4": 1.00},
        "marketing_per_agent": {"r1": 45_000.0, "r2": 60_000.0, "r3": 75_000.0, "r4": 90_000.0},
        "marketing_cash_cap": {"r1": 0.04, "r2": 0.05, "r3": 0.06, "r4": 0.07},
        "agent_total_mult": {"r1": 0.92, "r2": 0.96, "r3": 1.00, "r4": 1.02},
        "utilization_mult": {"r1": 0.94, "r2": 0.96, "r3": 0.98, "r4": 1.00},
        "price_round_mult": {"r1": 0.98, "r2": 0.97, "r3": 0.97, "r4": 0.96},
        "demand_per_agent": {"r1": 215.0, "r2": 245.0, "r3": 275.0, "r4": 310.0},
        "inventory_buffer": {"r1": 1.12, "r2": 1.12, "r3": 1.10, "r4": 1.08},
    },
    {
        "name": "premium",
        "market_bias": {"Shanghai": 1.26, "Chengdu": 0.94, "Wuhan": 0.88, "Wuxi": 0.98, "Ningbo": 0.94},
        "price_mult": 1.06,
        "marketing_mult": 0.68,
        "talent_mult": 0.92,
        "quality_mult": 1.80,
        "mgmt_mult": 1.55,
        "worker_salary_mult": 1.03,
        "engineer_salary_mult": 1.10,
        "focus_power": {"r1": 1.18, "r2": 1.16, "r3": 1.14, "r4": 1.10},
        "loan_mult": {"r1": 0.86, "r2": 0.88, "r3": 0.92, "r4": 0.94},
        "payroll_mult": {"r1": 0.84, "r2": 0.88, "r3": 0.92, "r4": 0.96},
        "marketing_round_mult": {"r1": 0.74, "r2": 0.78, "r3": 0.84, "r4": 0.90},
        "marketing_per_agent": {"r1": 30_000.0, "r2": 40_000.0, "r3": 50_000.0, "r4": 65_000.0},
        "marketing_cash_cap": {"r1": 0.03, "r2": 0.04, "r3": 0.05, "r4": 0.06},
        "agent_total_mult": {"r1": 0.82, "r2": 0.86, "r3": 0.92, "r4": 0.98},
        "utilization_mult": {"r1": 0.84, "r2": 0.88, "r3": 0.92, "r4": 0.96},
        "price_round_mult": {"r1": 1.06, "r2": 1.06, "r3": 1.05, "r4": 1.04},
        "demand_per_agent": {"r1": 130.0, "r2": 155.0, "r3": 180.0, "r4": 205.0},
        "inventory_buffer": {"r1": 1.04, "r2": 1.05, "r3": 1.05, "r4": 1.04},
    },
    {
        "name": "late_sprinter",
        "market_bias": {"Shanghai": 1.00, "Chengdu": 1.00, "Wuhan": 0.98, "Wuxi": 1.02, "Ningbo": 1.00},
        "price_mult": 0.98,
        "marketing_mult": 0.96,
        "talent_mult": 1.00,
        "quality_mult": 1.05,
        "mgmt_mult": 0.95,
        "worker_salary_mult": 0.99,
        "engineer_salary_mult": 1.01,
        "focus_power": {"r1": 1.02, "r2": 1.04, "r3": 1.10, "r4": 1.18},
        "loan_mult": {"r1": 0.82, "r2": 0.90, "r3": 1.08, "r4": 1.12},
        "payroll_mult": {"r1": 0.82, "r2": 0.90, "r3": 1.04, "r4": 1.12},
        "marketing_round_mult": {"r1": 0.66, "r2": 0.78, "r3": 1.04, "r4": 1.20},
        "marketing_per_agent": {"r1": 28_000.0, "r2": 45_000.0, "r3": 85_000.0, "r4": 130_000.0},
        "marketing_cash_cap": {"r1": 0.03, "r2": 0.04, "r3": 0.07, "r4": 0.10},
        "agent_total_mult": {"r1": 0.84, "r2": 0.92, "r3": 1.12, "r4": 1.24},
        "utilization_mult": {"r1": 0.82, "r2": 0.88, "r3": 1.02, "r4": 1.12},
        "price_round_mult": {"r1": 1.00, "r2": 0.99, "r3": 0.98, "r4": 0.95},
        "demand_per_agent": {"r1": 120.0, "r2": 155.0, "r3": 250.0, "r4": 330.0},
        "inventory_buffer": {"r1": 1.02, "r2": 1.04, "r3": 1.08, "r4": 1.10},
    },
)


ROUND_BASE: dict[str, dict[str, float]] = {
    "r1": {
        "loan": 0.82,
        "payroll": 0.34,
        "marketing": 0.03,
        "reserve": 0.14,
        "mgmt": 40.0,
        "quality": 2.2,
        "agent_total": 6.0,
        "eng_share": 0.27,
        "price_anchor": 23_800.0,
    },
    "r2": {
        "loan": 0.80,
        "payroll": 0.35,
        "marketing": 0.04,
        "reserve": 0.15,
        "mgmt": 55.0,
        "quality": 4.5,
        "agent_total": 8.0,
        "eng_share": 0.28,
        "price_anchor": 23_750.0,
    },
    "r3": {
        "loan": 0.78,
        "payroll": 0.37,
        "marketing": 0.05,
        "reserve": 0.16,
        "mgmt": 72.0,
        "quality": 6.8,
        "agent_total": 9.5,
        "eng_share": 0.29,
        "price_anchor": 23_350.0,
    },
    "r4": {
        "loan": 0.76,
        "payroll": 0.39,
        "marketing": 0.06,
        "reserve": 0.17,
        "mgmt": 90.0,
        "quality": 9.0,
        "agent_total": 11.0,
        "eng_share": 0.30,
        "price_anchor": 20_350.0,
    },
}


def deterministic_uniform(*parts: object) -> float:
    seed = "|".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    integer = int.from_bytes(digest[:8], "big", signed=False)
    return integer / float(2**64)


def clip_int(value: float, minimum: int) -> int:
    return max(int(round(value)), minimum)


def clip_float(value: float, minimum: float, maximum: float) -> float:
    return min(max(float(value), minimum), maximum)


def team_style(team: str) -> dict[str, Any]:
    index = int(deterministic_uniform(team, "peer-style") * len(ARCHETYPES)) % len(ARCHETYPES)
    return ARCHETYPES[index]


def normalized_weights(base: dict[str, float]) -> dict[str, float]:
    total = sum(base.values()) or 1.0
    return {market: float(base[market]) / total for market in MARKETS}


def round_style_multiplier(style: dict[str, Any], key: str, round_id: str, default: float = 1.0) -> float:
    values = style.get(key)
    if isinstance(values, dict):
        return float(values.get(round_id, default))
    if values is None:
        return float(default)
    return float(values)


def sharpened_weights(base: dict[str, float], power: float) -> dict[str, float]:
    sharpened = {market: max(float(base[market]) ** float(power), 1e-9) for market in MARKETS}
    return normalized_weights(sharpened)


def override_multiplier(overrides: dict[str, float] | None, key: str, default: float = 1.0) -> float:
    if overrides is None:
        return float(default)
    return float(overrides.get(key, default))


def integer_allocate(total: int, weights: dict[str, float], floor: int) -> dict[str, int]:
    allocation = {market: floor for market in MARKETS}
    remaining = max(total - floor * len(MARKETS), 0)
    if remaining <= 0:
        return allocation
    raw = {market: weights[market] * remaining for market in MARKETS}
    ints = {market: int(raw[market]) for market in MARKETS}
    for market in MARKETS:
        allocation[market] += ints[market]
    remainder = remaining - sum(ints.values())
    order = sorted(MARKETS, key=lambda market: raw[market] - ints[market], reverse=True)
    for market in order[:remainder]:
        allocation[market] += 1
    return allocation


def sort_rounds(df: pd.DataFrame) -> pd.DataFrame:
    return df.sort_values(["team", "round_id"], key=lambda s: s.map(ROUND_ORDER) if s.name == "round_id" else s)


def per_employee_capacity(
    simulator: ExschoolSimulator,
    *,
    round_id: str,
    salary: float,
    benchmark_avg: float,
    role: str,
) -> float:
    round_context = simulator.round_contexts[round_id]
    if role == "workers":
        reference_productivity = float(round_context.get("components_productivity", 24.0) or 24.0)
        reference_salary = float(
            round_context.get("worker_salary_actual", round_context.get("worker_avg_salary_actual", benchmark_avg)) or benchmark_avg
        )
        reference_avg = float(
            round_context.get("worker_avg_salary_actual", round_context.get("worker_salary_actual", reference_salary)) or reference_salary
        )
    else:
        reference_productivity = float(round_context.get("products_productivity", 9.0) or 9.0)
        reference_salary = float(
            round_context.get("engineer_salary_actual", round_context.get("engineer_avg_salary_actual", benchmark_avg)) or benchmark_avg
        )
        reference_avg = float(
            round_context.get("engineer_avg_salary_actual", round_context.get("engineer_salary_actual", reference_salary)) or reference_salary
        )

    reference_multiplier = productivity_multiplier(reference_salary, reference_avg)
    base_productivity = reference_productivity / max(reference_multiplier, 1e-9)
    return max(base_productivity * productivity_multiplier(salary, benchmark_avg), 1e-6)


def estimated_agent_change_cost(previous_agents: dict[str, int], agents_after: dict[str, int]) -> float:
    total = 0.0
    for market in MARKETS:
        before = int(previous_agents[market])
        after = int(agents_after[market])
        if after >= before:
            total += float(after - before) * 300_000.0
        else:
            total += float(before - after) * 100_000.0
    return total


R1_ATTACK_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "style": "real_top_dual_city",
        "products": 3150,
        "loan": 5_000_000.0,
        "management_index": 1320.0,
        "quality_index": 1.4,
        "markets": ("Shanghai", "Chengdu"),
        "marketing": 240_000.0,
        "price": 23_650.0,
    },
    {
        "style": "real_top_three_city",
        "products": 3000,
        "loan": 5_000_000.0,
        "management_index": 980.0,
        "quality_index": 2.0,
        "markets": ("Shanghai", "Chengdu", "Wuhan"),
        "marketing": 420_000.0,
        "price": 23_150.0,
    },
    {
        "style": "marketing_pressure",
        "products": 2650,
        "loan": 4_400_000.0,
        "management_index": 1150.0,
        "quality_index": 70.0,
        "markets": ("Shanghai", "Chengdu", "Wuhan"),
        "marketing": 1_200_000.0,
        "price": 22_400.0,
    },
    {
        "style": "premium_management",
        "products": 2200,
        "loan": 2_600_000.0,
        "management_index": 2100.0,
        "quality_index": 18.0,
        "markets": ("Shanghai",),
        "marketing": 650_000.0,
        "price": 24_200.0,
    },
    {
        "style": "central_low_price",
        "products": 2850,
        "loan": 5_000_000.0,
        "management_index": 1250.0,
        "quality_index": 4.0,
        "markets": ("Chengdu", "Wuhan"),
        "marketing": 520_000.0,
        "price": 22_000.0,
    },
    {
        "style": "east_coast_flanker",
        "products": 2350,
        "loan": 3_200_000.0,
        "management_index": 1450.0,
        "quality_index": 28.0,
        "markets": ("Shanghai", "Wuxi", "Ningbo"),
        "marketing": 780_000.0,
        "price": 22_850.0,
    },
)


R1_MARKET_COMBOS: tuple[tuple[str, ...], ...] = (
    ("Shanghai", "Chengdu", "Wuhan"),
    ("Wuxi", "Ningbo"),
    ("Shanghai", "Wuxi", "Ningbo"),
    ("Chengdu", "Wuhan"),
    ("Shanghai",),
    ("Wuhan", "Wuxi", "Ningbo"),
    ("Chengdu", "Ningbo"),
    ("Shanghai", "Wuhan", "Wuxi"),
)


def build_r1_attack_decision(
    simulator: ExschoolSimulator,
    *,
    team: str,
    state: Any,
    overrides: dict[str, float] | None = None,
) -> tuple[SimulationInput, dict[str, Any]]:
    context = simulator._context_for_company_state("r1", team, state)
    peer_ids = [peer for peer in simulator.team_ids if peer != OUR_TEAM]
    peer_index = peer_ids.index(team) if team in peer_ids else int(team)
    blueprint = R1_ATTACK_BLUEPRINTS[peer_index % len(R1_ATTACK_BLUEPRINTS)]
    style = team_style(team)
    override_price = override_multiplier(overrides, "price")
    override_payroll = override_multiplier(overrides, "payroll")
    override_utilization = override_multiplier(overrides, "utilization")
    override_marketing = override_multiplier(overrides, "marketing")
    override_agents = override_multiplier(overrides, "agents")
    loan_override = override_multiplier(overrides, "loan")

    jitter = 0.92 + 0.18 * deterministic_uniform(team, "r1-attack", "products")
    products_planned = clip_int(
        float(blueprint["products"]) * jitter * override_payroll * override_utilization,
        700,
    )
    loan_delta = round(
        min(
            float(context["loan_limit"]),
            float(blueprint["loan"]) * (0.96 + 0.08 * deterministic_uniform(team, "r1-attack", "loan")) * loan_override,
        ),
        2,
    )

    worker_salary = round(
        clip_float(
            float(context["worker_avg_salary_prev"])
            * (1.00 + 0.035 * deterministic_uniform(team, "r1-attack", "worker-salary")),
            2_000.0,
            9_000.0,
        ),
        2,
    )
    engineer_salary = round(
        clip_float(
            float(context["engineer_avg_salary_prev"])
            * (1.00 + 0.035 * deterministic_uniform(team, "r1-attack", "engineer-salary")),
            3_800.0,
            9_800.0,
        ),
        2,
    )
    workers = clip_int(products_planned / 3.52, 210)
    engineers = clip_int(products_planned / 9.12, 80)
    management_index = float(blueprint["management_index"]) * (0.90 + 0.20 * deterministic_uniform(team, "r1-attack", "management"))
    quality_index = float(blueprint["quality_index"]) * (0.85 + 0.30 * deterministic_uniform(team, "r1-attack", "quality"))
    management_investment = round((workers + engineers) * management_index, 2)
    quality_investment = round(products_planned * quality_index, 2)

    selected_markets = R1_MARKET_COMBOS[peer_index % len(R1_MARKET_COMBOS)]
    agent_total = max(1, int(round(len(selected_markets) * override_agents)))
    agent_total = min(agent_total, len(selected_markets))
    active_markets = selected_markets[:agent_total]
    weights = normalized_weights(
        {
            market: (float(style["market_bias"][market]) if market in active_markets else 0.0)
            + (0.01 if market in active_markets else 0.0)
            for market in MARKETS
        }
    )
    total_marketing = max(
        float(blueprint["marketing"])
        * (0.88 + 0.24 * deterministic_uniform(team, "r1-attack", "marketing"))
        * override_marketing,
        90_000.0 * len(active_markets),
    )
    market_decisions: dict[str, MarketDecision] = {}
    for market in MARKETS:
        previous_agents = int(context["market_defaults"][market]["previous_agents"])
        if market in active_markets:
            price = round(
                clip_float(
                    float(blueprint["price"])
                    * override_price
                    * (0.985 + 0.035 * deterministic_uniform(team, "r1-attack", market, "price")),
                    19_000.0,
                    24_800.0,
                ),
                2,
            )
            market_decisions[market] = MarketDecision(
                agent_change=1 - previous_agents,
                marketing_investment=round(total_marketing * weights[market], 2),
                price=price,
                subscribed_market_report=True,
            )
        else:
            market_decisions[market] = MarketDecision(
                agent_change=-previous_agents,
                marketing_investment=0.0,
                price=0.0,
                subscribed_market_report=False,
            )

    decision = SimulationInput(
        round_id="r1",
        loan_delta=loan_delta,
        workers=workers,
        engineers=engineers,
        worker_salary=worker_salary,
        engineer_salary=engineer_salary,
        management_investment=management_investment,
        quality_investment=quality_investment,
        research_investment=0.0,
        products_planned=products_planned,
        market_decisions=market_decisions,
    )
    signature = {
        "style": f"r1_{blueprint['style']}",
        "loan_delta": loan_delta,
        "workers": workers,
        "engineers": engineers,
        "products_planned": products_planned,
        "management_investment": management_investment,
        "quality_investment": quality_investment,
        "agent_total": len(active_markets),
        "marketing_total": round(sum(item.marketing_investment for item in market_decisions.values()), 2),
        "avg_price": round(
            sum(item.price for item in market_decisions.values() if item.price > 0) / max(len(active_markets), 1),
            2,
        ),
        "overrides": dict(overrides or {}),
    }
    return decision, signature


def build_peer_decision(
    simulator: ExschoolSimulator,
    *,
    team: str,
    round_id: str,
    state: Any,
    overrides: dict[str, float] | None = None,
) -> tuple[SimulationInput, dict[str, Any]]:
    if round_id == "r1":
        return build_r1_attack_decision(simulator, team=team, state=state, overrides=overrides)

    style = team_style(team)
    round_base = ROUND_BASE[round_id]
    context = simulator._context_for_company_state(round_id, team, state)
    focus_power = round_style_multiplier(style, "focus_power", round_id)
    loan_style_mult = round_style_multiplier(style, "loan_mult", round_id)
    payroll_style_mult = round_style_multiplier(style, "payroll_mult", round_id)
    marketing_style_mult = round_style_multiplier(style, "marketing_round_mult", round_id)
    marketing_per_agent = round_style_multiplier(style, "marketing_per_agent", round_id, 120_000.0)
    marketing_cash_cap = round_style_multiplier(style, "marketing_cash_cap", round_id, 0.10)
    agent_style_mult = round_style_multiplier(style, "agent_total_mult", round_id)
    utilization_style_mult = round_style_multiplier(style, "utilization_mult", round_id)
    price_style_mult = round_style_multiplier(style, "price_round_mult", round_id)
    demand_per_agent = round_style_multiplier(style, "demand_per_agent", round_id, 180.0)
    inventory_buffer = round_style_multiplier(style, "inventory_buffer", round_id, 1.08)
    worker_salary_style_mult = round_style_multiplier(style, "worker_salary_mult", round_id, 1.0)
    engineer_salary_style_mult = round_style_multiplier(style, "engineer_salary_mult", round_id, 1.0)
    loan_override = override_multiplier(overrides, "loan")
    payroll_override = override_multiplier(overrides, "payroll")
    utilization_override = override_multiplier(overrides, "utilization")
    marketing_override = override_multiplier(overrides, "marketing")
    agent_override = override_multiplier(overrides, "agents")
    price_override = override_multiplier(overrides, "price")

    starting_cash = float(context["starting_cash"])
    starting_debt = float(context["starting_debt"])
    net_assets = starting_cash - starting_debt
    health = clip_float((net_assets + 12_000_000.0) / 24_000_000.0, 0.74, 1.18)

    loan_factor = clip_float(
        round_base["loan"]
        * loan_style_mult
        * loan_override
        * (0.95 + 0.10 * deterministic_uniform(team, round_id, "loan"))
        * (health**0.08),
        0.46,
        0.94,
    )
    loan_delta = round(float(context["loan_limit"]) * loan_factor, 2)
    working_cash = starting_cash + loan_delta
    reserve = working_cash * round_base["reserve"]

    worker_salary = round(
        clip_float(
            float(context["worker_avg_salary_prev"])
            * worker_salary_style_mult
            * (0.99 + 0.04 * deterministic_uniform(team, round_id, "worker-salary")),
            2_000.0,
            9_000.0,
        ),
        2,
    )
    engineer_salary = round(
        clip_float(
            float(context["engineer_avg_salary_prev"])
            * engineer_salary_style_mult
            * (0.99 + 0.04 * deterministic_uniform(team, round_id, "engineer-salary")),
            3_800.0,
            9_800.0,
        ),
        2,
    )

    previous_agents = {market: int(context["market_defaults"][market]["previous_agents"]) for market in MARKETS}
    weight_seed = {
        market: float(style["market_bias"][market])
        * (0.92 + 0.18 * deterministic_uniform(team, round_id, market, "market-weight"))
        for market in MARKETS
    }
    market_weights = sharpened_weights(weight_seed, focus_power)
    target_agent_total = clip_int(
        round_base["agent_total"] * agent_style_mult * agent_override
        + round((deterministic_uniform(team, round_id, "agent-total") - 0.5) * 4.0)
        + (1 if round_id in {"r3", "r4"} and style["name"] in {"late_sprinter", "national_brand"} else 0),
        len(MARKETS),
    )
    agents_after = integer_allocate(target_agent_total, market_weights, 1)
    agent_change_cost_est = estimated_agent_change_cost(previous_agents, agents_after)

    preliminary_marketing_total = max(
        target_agent_total
        * marketing_per_agent
        * marketing_style_mult
        * marketing_override
        * float(style["marketing_mult"])
        * (0.90 + 0.18 * deterministic_uniform(team, round_id, "marketing")),
        60_000.0 if round_id == "r1" else 90_000.0,
    )
    preliminary_marketing_total = min(preliminary_marketing_total, working_cash * marketing_cash_cap)
    preliminary_marketing_total = max(preliminary_marketing_total, 30_000.0 * len(MARKETS))

    market_prices: dict[str, float] = {}
    for market in MARKETS:
        market_prices[market] = round(
            clip_float(
                round_base["price_anchor"]
                * price_style_mult
                * float(style["price_mult"])
                * price_override
                * (0.985 + 0.04 * deterministic_uniform(team, round_id, market, "price")),
                6_000.0,
                25_000.0,
            ),
            2,
        )
    avg_price = sum(market_prices.values()) / len(MARKETS)

    management_index = round_base["mgmt"] * float(style["mgmt_mult"]) * (
        0.90 + 0.22 * deterministic_uniform(team, round_id, "management")
    )
    quality_index = round_base["quality"] * float(style["quality_mult"]) * (
        0.90 + 0.20 * deterministic_uniform(team, round_id, "quality")
    )

    previous_inventory = float(context.get("product_inventory_prev", 0.0) or 0.0)
    previous_management_index = float(context.get("campaign_previous_management_index", 0.0) or 0.0)
    previous_quality_index = float(context.get("campaign_previous_quality_index", 0.0) or 0.0)
    price_factor = clip_float((round_base["price_anchor"] / max(avg_price, 1.0)) ** 0.58, 0.76, 1.18)
    marketing_per_agent_effective = preliminary_marketing_total / max(float(target_agent_total), 1.0)
    marketing_factor = clip_float(0.72 + 0.18 * math.log1p(marketing_per_agent_effective / 25_000.0), 0.72, 1.18)
    capability_factor = clip_float(
        0.84 + 0.05 * math.log1p(management_index) + 0.04 * math.log1p(quality_index),
        0.86,
        1.30,
    )
    carry_factor = clip_float(
        0.94 + 0.015 * math.log1p(previous_management_index) + 0.012 * math.log1p(previous_quality_index),
        0.94,
        1.16,
    )
    demand_jitter = 0.94 + 0.12 * deterministic_uniform(team, round_id, "demand")
    base_target_sales = (
        float(target_agent_total)
        * demand_per_agent
        * health
        * payroll_style_mult
        * payroll_override
        * price_factor
        * marketing_factor
        * capability_factor
        * carry_factor
        * demand_jitter
    )
    min_products = 80.0 if round_id == "r1" else 120.0
    desired_new_products = max(base_target_sales * inventory_buffer - previous_inventory, min_products)
    utilization = clip_float(
        (0.88 + 0.10 * deterministic_uniform(team, round_id, "utilization"))
        * utilization_style_mult
        * utilization_override,
        0.78,
        0.98,
    )
    worker_unit_capacity = per_employee_capacity(
        simulator,
        round_id=round_id,
        salary=worker_salary,
        benchmark_avg=float(context["worker_avg_salary_prev"]),
        role="workers",
    )
    engineer_unit_capacity = per_employee_capacity(
        simulator,
        round_id=round_id,
        salary=engineer_salary,
        benchmark_avg=float(context["engineer_avg_salary_prev"]),
        role="engineers",
    )
    patent_multiplier = patent_cost_multiplier(int(context.get("active_patents_prev", 0) or 0))
    estimated_unit_cost = patent_multiplier * (
        7.0 * float(context["component_material_price"]) + float(context["product_material_price"])
    )

    scale = 1.0
    marketing_total = preliminary_marketing_total
    workers = 60
    engineers = 18
    products_planned = int(min_products)
    management_investment = 0.0
    quality_investment = 0.0
    for _ in range(6):
        scaled_products = max(desired_new_products * scale, min_products)
        required_capacity = scaled_products / max(utilization, 0.6)
        workers = clip_int(
            required_capacity * 7.0 / max(worker_unit_capacity, 1e-6) * float(style["talent_mult"]),
            60,
        )
        engineers = clip_int(required_capacity / max(engineer_unit_capacity, 1e-6), 18)
        worker_plan = workforce_plan(
            requested_total=workers,
            requested_salary=worker_salary,
            benchmark_average_salary=float(context["worker_avg_salary_prev"]),
            previous_recent=int(context.get("worker_recent_prev", context["workers_actual"])),
            previous_mature=int(context.get("worker_mature_prev", 0)),
            previous_experienced=int(context.get("worker_experienced_prev", 0)),
        )
        engineer_plan = workforce_plan(
            requested_total=engineers,
            requested_salary=engineer_salary,
            benchmark_average_salary=float(context["engineer_avg_salary_prev"]),
            previous_recent=int(context.get("engineer_recent_prev", context["engineers_actual"])),
            previous_mature=int(context.get("engineer_mature_prev", 0)),
            previous_experienced=int(context.get("engineer_experienced_prev", 0)),
        )
        actual_workers = int(worker_plan["working"])
        actual_engineers = int(engineer_plan["working"])
        worker_product_capacity = actual_workers * worker_unit_capacity / 7.0
        engineer_product_capacity = actual_engineers * engineer_unit_capacity
        product_capacity = max(min(worker_product_capacity, engineer_product_capacity), 0.0)
        products_planned = clip_int(min(scaled_products, product_capacity * utilization), int(min_products))
        management_investment = round((actual_workers + actual_engineers) * management_index, 2)
        quality_denominator = previous_inventory * 1.2 + float(products_planned)
        quality_investment = round(max(quality_denominator, 0.0) * quality_index, 2)

        payroll_commitment = actual_workers * worker_salary * 3.0 + actual_engineers * engineer_salary * 3.0
        material_commitment = float(products_planned) * estimated_unit_cost
        total_commitment = (
            payroll_commitment
            + material_commitment
            + agent_change_cost_est
            + marketing_total
            + management_investment
            + quality_investment
        )
        budget_ceiling = max((working_cash - reserve) * 0.92, working_cash * round_base["payroll"] * 1.35)
        if total_commitment <= budget_ceiling or scale <= 0.45:
            break
        shrink = clip_float(math.sqrt(max(budget_ceiling, 1.0) / max(total_commitment, 1.0)) * 0.97, 0.70, 0.95)
        scale *= shrink
        marketing_total *= clip_float(shrink + 0.10, 0.78, 0.97)

    marketing_total = min(marketing_total, working_cash * marketing_cash_cap)
    marketing_total = max(marketing_total, 30_000.0 * len(MARKETS))

    market_decisions: dict[str, MarketDecision] = {}
    for market in MARKETS:
        price = market_prices[market]
        marketing = round(max(marketing_total * market_weights[market], 12_000.0), 2)
        market_decisions[market] = MarketDecision(
            agent_change=int(agents_after[market] - previous_agents[market]),
            marketing_investment=marketing,
            price=price,
            subscribed_market_report=True,
        )

    decision = SimulationInput(
        round_id=round_id,
        loan_delta=loan_delta,
        workers=workers,
        engineers=engineers,
        worker_salary=worker_salary,
        engineer_salary=engineer_salary,
        management_investment=management_investment,
        quality_investment=quality_investment,
        research_investment=0.0,
        products_planned=products_planned,
        market_decisions=market_decisions,
    )
    signature = {
        "style": style["name"],
        "loan_delta": loan_delta,
        "workers": workers,
        "engineers": engineers,
        "products_planned": products_planned,
        "management_investment": management_investment,
        "quality_investment": quality_investment,
        "agent_total": sum(agents_after.values()),
        "marketing_total": round(sum(item.marketing_investment for item in market_decisions.values()), 2),
        "avg_price": round(sum(item.price for item in market_decisions.values()) / len(MARKETS), 2),
        "overrides": dict(overrides or {}),
    }
    return decision, signature


def decision_to_row(team: str, decision: SimulationInput, context: dict[str, Any]) -> dict[str, Any]:
    row = {
        "team": team,
        "round_id": decision.round_id,
        "loan_delta": decision.loan_delta,
        "products_planned": decision.products_planned,
        "quality_investment": decision.quality_investment,
        "workers": decision.workers,
        "engineers": decision.engineers,
        "worker_salary": decision.worker_salary,
        "engineer_salary": decision.engineer_salary,
        "management_investment": decision.management_investment,
    }
    for market in MARKETS:
        slug = market.lower()
        market_decision = decision.market_decisions[market]
        before = int(context["market_defaults"][market]["previous_agents"])
        row[f"{slug}_selected"] = 1
        row[f"{slug}_agents_before"] = before
        row[f"{slug}_agent_change"] = int(market_decision.agent_change)
        row[f"{slug}_agents_after"] = before + int(market_decision.agent_change)
        row[f"{slug}_marketing_investment"] = float(market_decision.marketing_investment)
        row[f"{slug}_price"] = float(market_decision.price)
    return row


def decision_marketing_total(decision: SimulationInput) -> float:
    return float(sum(float(item.marketing_investment) for item in decision.market_decisions.values()))


def decision_positive_agent_total(decision: SimulationInput) -> int:
    return int(sum(max(int(item.agent_change), 0) for item in decision.market_decisions.values()))


LEAD_MARKET_TEAMS: set[str] = set()
TEAM_HOME_MARKETS = {
    "1": "Wuhan",
    "2": "Wuxi",
    "3": "Chengdu",
    "4": "Shanghai",
    "5": "Wuhan",
    "6": "Shanghai",
    "7": "Shanghai",
    "8": "Wuxi",
    "9": "Chengdu",
    "10": "Chengdu",
    "12": "Wuhan",
    "14": "Shanghai",
    "15": "Shanghai",
    "16": "Shanghai",
    "17": "Shanghai",
    "18": "Ningbo",
    "19": "Wuxi",
    "20": "Chengdu",
    "21": "Wuhan",
    "22": "Shanghai",
    "23": "Shanghai",
    "24": "Shanghai",
}


def _apply_home_market_plan(
    row: dict[str, Any],
    *,
    home_market: str,
    products: int,
    workers: int,
    engineers: int,
    management_investment: float,
    marketing_investment: float,
    price: float,
) -> dict[str, Any]:
    round_id = str(row.get("round_id", "r1"))
    replacement = {
        **row,
        "loan_delta": 0.0,
        "products_planned": int(products),
        "workers": int(workers),
        "engineers": int(engineers),
        "worker_salary": 3_000.0,
        "engineer_salary": 5_000.0,
        "management_investment": float(management_investment),
        "quality_investment": 0.0,
        "research_investment": 0.0,
    }
    for market in MARKETS:
        slug = market.lower()
        active = market == home_market
        before = 1 if active and round_id != "r1" else 0
        after = 1 if active else 0
        replacement[f"{slug}_selected"] = 0
        replacement[f"{slug}_agents_before"] = before
        replacement[f"{slug}_agent_change"] = after - before
        replacement[f"{slug}_agents_after"] = after
        replacement[f"{slug}_marketing_investment"] = float(marketing_investment) if active else 0.0
        replacement[f"{slug}_price"] = float(price) if active else 0.0
    return replacement


def apply_elite_efficiency_playbook(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use affordable home-market decisions instead of loss-making saturation bots."""
    updated: list[dict[str, Any]] = []
    for row in rows:
        team = str(row["team"])
        target_market = "Chengdu" if team in LEAD_MARKET_TEAMS or team == "24" else "Shanghai"
        if team in LEAD_MARKET_TEAMS:
            updated.append(
                _apply_home_market_plan(
                    row,
                    home_market=target_market,
                    products=500,
                    workers=150,
                    engineers=60,
                    management_investment=900_000.0,
                    marketing_investment=300_000.0,
                    price=18_000.0,
                )
            )
        else:
            updated.append(
                _apply_home_market_plan(
                    row,
                    home_market=target_market,
                    products=120,
                    workers=45,
                    engineers=18,
                    management_investment=700_000.0,
                    marketing_investment=80_000.0,
                    price=18_000.0,
                )
            )
    return updated


def stabilize_round_decisions(
    simulator: ExschoolSimulator,
    *,
    round_id: str,
    calibration_states: dict[str, Any],
    contexts_by_team: dict[str, dict[str, Any]],
    round_overrides: dict[str, dict[str, float]],
) -> tuple[dict[str, SimulationInput], dict[str, dict[str, Any]]]:
    round_decisions: dict[str, SimulationInput] = {}
    round_signatures: dict[str, dict[str, Any]] = {}
    for _ in range(8):
        round_decisions = {}
        round_signatures = {}
        for team in simulator.team_ids:
            decision, signature = build_peer_decision(
                simulator,
                team=team,
                round_id=round_id,
                state=calibration_states.get(team),
                overrides=round_overrides[team],
            )
            round_decisions[team] = decision
            round_signatures[team] = signature

        prev_worker_avg, prev_engineer_avg = simulator._previous_global_average_salary(calibration_states)
        benchmark_worker_avg, benchmark_engineer_avg = simulator._current_global_average_salary_for_multiplayer(
            round_decisions,
            prev_worker_avg,
            prev_engineer_avg,
        )
        effective_by_team = {
            team: simulator._apply_cash_break_to_decision(
                round_decisions[team],
                contexts_by_team[team],
                starting_cash=float(contexts_by_team[team]["starting_cash"]),
                starting_debt=float(contexts_by_team[team]["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )
            for team in simulator.team_ids
        }

        stable = True
        for team in simulator.team_ids:
            requested = round_decisions[team]
            effective = effective_by_team[team]
            requested_marketing = decision_marketing_total(requested)
            effective_marketing = decision_marketing_total(effective)
            requested_agents = decision_positive_agent_total(requested)
            effective_agents = decision_positive_agent_total(effective)
            management_ratio = (
                float(effective.management_investment) / float(requested.management_investment)
                if float(requested.management_investment) > 0
                else 1.0
            )
            quality_ratio = (
                float(effective.quality_investment) / float(requested.quality_investment)
                if float(requested.quality_investment) > 0
                else 1.0
            )
            marketing_ratio = (
                effective_marketing / requested_marketing
                if requested_marketing > 0
                else 1.0
            )
            agent_ratio = (
                float(effective_agents) / float(requested_agents)
                if requested_agents > 0
                else 1.0
            )
            severe = (
                management_ratio < 0.85
                or quality_ratio < 0.85
                or marketing_ratio < 0.70
                or agent_ratio < 0.80
            )
            needs_adjustment = (
                severe
                or management_ratio < 0.97
                or quality_ratio < 0.97
                or marketing_ratio < 0.93
                or agent_ratio < 0.93
            )
            if not needs_adjustment:
                continue

            stable = False
            overrides = round_overrides[team]
            if severe:
                overrides["payroll"] *= 0.86
                overrides["utilization"] *= 0.84
                overrides["marketing"] *= 0.72
                overrides["agents"] *= 0.84
            else:
                if management_ratio < 0.97 or quality_ratio < 0.97:
                    overrides["payroll"] *= 0.93
                    overrides["utilization"] *= 0.91
                if marketing_ratio < 0.93:
                    overrides["marketing"] *= 0.86
                if agent_ratio < 0.93:
                    overrides["agents"] *= 0.90
            if management_ratio < 1.0 or quality_ratio < 1.0:
                overrides["loan"] = min(overrides["loan"] * 1.03, 1.12)
            overrides["payroll"] = max(overrides["payroll"], 0.52)
            overrides["utilization"] = max(overrides["utilization"], 0.55)
            overrides["marketing"] = max(overrides["marketing"], 0.32)
            overrides["agents"] = max(overrides["agents"], 0.48)

        if stable:
            break

    return round_decisions, round_signatures


def apply_profit_safety_overrides(
    *,
    style_name: str,
    round_id: str,
    sales_revenue: float,
    net_profit: float,
    net_assets: float,
    overrides: dict[str, float],
) -> None:
    loss_ratio = min(abs(net_profit) / max(sales_revenue, 1.0), 2.0)
    low_revenue = sales_revenue < 8_000_000.0
    severe_loss = loss_ratio > 0.45 or net_profit < -2_500_000.0
    critical_loss = (
        loss_ratio > 0.75
        or net_profit < -4_000_000.0
        or (low_revenue and sales_revenue < 6_500_000.0)
    )
    marketing_floor = 0.18
    agent_floor = 0.34 if critical_loss else 0.40
    payroll_floor = 0.44 if critical_loss else 0.48
    utilization_floor = 0.48 if critical_loss else 0.52
    price_floor = 0.80 if critical_loss else 0.84

    if low_revenue:
        if round_id == "r1":
            overrides["price"] *= max(0.78, 0.93 - 0.06 * loss_ratio)
            overrides["marketing"] *= min(1.45, 1.12 + 0.12 * loss_ratio)
            overrides["agents"] *= min(1.18, 1.04 + 0.05 * loss_ratio)
            overrides["payroll"] *= max(0.74, 0.92 - 0.08 * loss_ratio)
            overrides["utilization"] *= max(0.74, 0.92 - 0.08 * loss_ratio)
        else:
            overrides["price"] *= max(0.86, 0.96 - 0.07 * loss_ratio)
            overrides["marketing"] *= max(0.90, 0.98 - 0.04 * loss_ratio)
            overrides["agents"] *= max(0.88, 0.98 - 0.05 * loss_ratio)
            overrides["payroll"] *= max(0.68, 0.88 - 0.12 * loss_ratio)
            overrides["utilization"] *= max(0.68, 0.86 - 0.10 * loss_ratio)
    else:
        overrides["marketing"] *= max(0.68, 0.92 - 0.10 * loss_ratio)
        overrides["agents"] *= max(0.78, 0.95 - 0.08 * loss_ratio)
        overrides["payroll"] *= max(0.76, 0.93 - 0.08 * loss_ratio)
        overrides["utilization"] *= max(0.74, 0.92 - 0.07 * loss_ratio)
        overrides["price"] *= max(0.90, 0.98 - 0.04 * loss_ratio)

    if style_name == "late_sprinter" and round_id == "r2":
        overrides["price"] *= 0.97
        overrides["payroll"] *= 0.94
        overrides["utilization"] *= 0.95
    if style_name == "national_brand" and round_id == "r3":
        overrides["agents"] *= 0.80 if critical_loss else 0.86
        overrides["marketing"] *= 0.72 if critical_loss else 0.82
        overrides["payroll"] *= 0.78 if critical_loss else 0.84
        overrides["price"] *= 0.95 if critical_loss else 0.97
        overrides["loan"] *= 0.90 if critical_loss else 0.95
    if low_revenue and style_name == "late_sprinter" and round_id == "r2":
        marketing_floor = max(marketing_floor, 0.62)
        agent_floor = max(agent_floor, 0.76)

    if critical_loss:
        overrides["loan"] *= 0.90
    elif severe_loss or sales_revenue < 5_000_000.0 or net_assets < 10_000_000.0:
        overrides["loan"] *= 0.95

    overrides["marketing"] = max(overrides["marketing"], marketing_floor)
    overrides["agents"] = max(overrides["agents"], agent_floor)
    overrides["payroll"] = max(overrides["payroll"], payroll_floor)
    overrides["utilization"] = max(overrides["utilization"], utilization_floor)
    overrides["loan"] = max(overrides["loan"], 0.64)
    overrides["price"] = max(overrides["price"], price_floor)
    if round_id == "r1" and low_revenue:
        overrides["marketing"] = min(max(overrides["marketing"], 0.90), 2.40)
        overrides["agents"] = min(max(overrides["agents"], 0.84), 1.30)
        overrides["payroll"] = max(overrides["payroll"], 0.70)
        overrides["utilization"] = max(overrides["utilization"], 0.70)
        overrides["price"] = max(overrides["price"], 0.72)


def generate() -> tuple[pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    source_path = SOURCE_DECISIONS_XLSX if SOURCE_DECISIONS_XLSX.exists() else LEGACY_DECISIONS_XLSX
    original_df = pd.read_excel(source_path)
    original_df["team"] = original_df["team"].astype(str)
    original_df["round_id"] = original_df["round_id"].astype(str)

    simulator = ExschoolSimulator()
    calibration_states = {team: None for team in simulator.team_ids}
    calibration_frames: list[pd.DataFrame] = []
    generated_rows: list[dict[str, Any]] = []
    validation_rows: list[dict[str, Any]] = []
    self_play_rounds: list[dict[str, Any]] = []

    for round_id in simulator.available_rounds():
        round_overrides = {
            team: {
                "loan": 1.0,
                "payroll": 1.0,
                "utilization": 1.0,
                "marketing": 1.0,
                "agents": 1.0,
                "price": 1.0,
            }
            for team in simulator.team_ids
        }
        round_decisions: dict[str, SimulationInput] = {}
        round_signatures: dict[str, dict[str, Any]] = {}
        contexts_by_team = {
            team: simulator._context_for_company_state(
                round_id,
                team,
                calibration_states.get(team),
                current_home_city=CALIBRATION_HOME_CITY if team == OUR_TEAM else None,
            )
            for team in simulator.team_ids
        }

        round_decisions, round_signatures = stabilize_round_decisions(
            simulator,
            round_id=round_id,
            calibration_states=calibration_states,
            contexts_by_team=contexts_by_team,
            round_overrides=round_overrides,
        )

        selected_rows: list[dict[str, Any]] = []
        selected_report: dict[str, Any] | None = None
        selected_next_states: dict[str, Any] | None = None
        for _ in range(6):
            round_rows = [
                decision_to_row(team, round_decisions[team], contexts_by_team[team])
                for team in simulator.team_ids
            ]
            candidate_frame = pd.DataFrame(round_rows)
            simulator.fixed_decisions_df = pd.concat([*calibration_frames, candidate_frame], ignore_index=True)

            candidate_report, candidate_next_states = simulator._simulate_multiplayer_report(
                round_decisions[OUR_TEAM],
                simulator._context_for_company_state(
                    round_id,
                    OUR_TEAM,
                    calibration_states.get(OUR_TEAM),
                    current_home_city=CALIBRATION_HOME_CITY,
                ),
                mode="campaign",
                team_states=calibration_states,
            )
            standings_by_team_candidate = {
                str(row["team"]): row for row in candidate_report["all_company_standings"]
            }

            adjusted_for_profit = False
            for team in simulator.team_ids:
                if team == OUR_TEAM:
                    continue
                standing = standings_by_team_candidate.get(team, {})
                net_profit = float(standing.get("net_profit", 0.0) or 0.0)
                sales_revenue = float(standing.get("sales_revenue", 0.0) or 0.0)
                net_assets = float(standing.get("net_assets", 0.0) or 0.0)
                if net_profit >= 0:
                    continue

                overrides = round_overrides[team]
                apply_profit_safety_overrides(
                    style_name=round_signatures[team]["style"],
                    round_id=round_id,
                    sales_revenue=sales_revenue,
                    net_profit=net_profit,
                    net_assets=net_assets,
                    overrides=overrides,
                )
                adjusted_for_profit = True

            if not adjusted_for_profit:
                selected_rows = round_rows
                selected_report = candidate_report
                selected_next_states = candidate_next_states
                break

            round_decisions, round_signatures = stabilize_round_decisions(
                simulator,
                round_id=round_id,
                calibration_states=calibration_states,
                contexts_by_team=contexts_by_team,
                round_overrides=round_overrides,
            )

        if selected_report is None or selected_next_states is None:
            selected_rows = round_rows
            selected_report = candidate_report
            selected_next_states = candidate_next_states

        round_frame = pd.DataFrame(selected_rows)
        calibration_frames.append(round_frame)
        simulator.fixed_decisions_df = pd.concat(calibration_frames, ignore_index=True)
        calibration_report = selected_report
        calibration_states = selected_next_states

        standings_by_team = {
            str(row["team"]): row for row in calibration_report["all_company_standings"]
        }
        round_snapshot = []
        for team in simulator.team_ids:
            state = calibration_states[team]
            signature = round_signatures[team]
            standing = standings_by_team.get(team, {})
            active_markets = sum(1 for market in MARKETS if int(state.market_agents_after.get(market, 0)) > 0) if state else 0
            validation_rows.append(
                {
                    "team": team,
                    "round_id": round_id,
                    "style": signature["style"],
                    "loan_delta": signature["loan_delta"],
                    "workers": signature["workers"],
                    "engineers": signature["engineers"],
                    "products_planned": signature["products_planned"],
                    "management_investment": signature["management_investment"],
                    "quality_investment": signature["quality_investment"],
                    "management_index": float(state.previous_management_index) if state is not None else 0.0,
                    "quality_index": float(state.previous_quality_index) if state is not None else 0.0,
                    "ending_cash": float(state.current_cash) if state is not None else 0.0,
                    "ending_debt": float(state.current_debt) if state is not None else 0.0,
                    "net_assets": float(standing.get("net_assets", 0.0) or 0.0),
                    "sales_revenue": float(standing.get("sales_revenue", 0.0) or 0.0),
                    "net_profit": float(standing.get("net_profit", 0.0) or 0.0),
                    "rank": int(standing.get("rank", 0) or 0),
                    "agent_total": int(signature["agent_total"]),
                    "marketing_total": float(signature["marketing_total"]),
                    "avg_price": float(signature["avg_price"]),
                    "active_markets": active_markets,
                    "all_markets_active": active_markets == len(MARKETS),
                }
            )
            round_snapshot.append(
                {
                    "team": team,
                    "rank": int(standing.get("rank", 0) or 0),
                    "net_assets": float(standing.get("net_assets", 0.0) or 0.0),
                    "sales_revenue": float(standing.get("sales_revenue", 0.0) or 0.0),
                    "net_profit": float(standing.get("net_profit", 0.0) or 0.0),
                    "management_index": float(state.previous_management_index) if state is not None else 0.0,
                    "quality_index": float(state.previous_quality_index) if state is not None else 0.0,
                }
            )

        self_play_rounds.append(
            {
                "round_id": round_id,
                "standings": sorted(round_snapshot, key=lambda item: item["rank"] or 999),
            }
        )

        for row in round_rows:
            if str(row["team"]) != OUR_TEAM:
                generated_rows.append(row)

    generated_by_key = {
        (str(row["team"]), str(row["round_id"])): row for row in apply_elite_efficiency_playbook(generated_rows)
    }
    final_rows: list[dict[str, Any]] = []
    for _, original_row in original_df.iterrows():
        team = str(original_row["team"])
        round_id = str(original_row["round_id"])
        if team == OUR_TEAM:
            final_rows.append(original_row.to_dict())
            continue
        generated = generated_by_key[(team, round_id)].copy()
        final_rows.append({column: generated.get(column, original_row.get(column)) for column in original_df.columns})

    out_df = pd.DataFrame(final_rows)[original_df.columns]
    return out_df, validation_rows, {"rounds": self_play_rounds}


def main() -> None:
    source_path = SOURCE_DECISIONS_XLSX if SOURCE_DECISIONS_XLSX.exists() else LEGACY_DECISIONS_XLSX
    original_df = pd.read_excel(source_path)
    original_df["team"] = original_df["team"].astype(str)
    original_df["round_id"] = original_df["round_id"].astype(str)
    original_team13 = sort_rounds(original_df[original_df["team"] == OUR_TEAM].copy()).reset_index(drop=True)

    out_df, validation_rows, self_play = generate()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_df.to_excel(SMART_DECISIONS_XLSX, index=False)

    validation_df = sort_rounds(pd.DataFrame(validation_rows)).reset_index(drop=True)
    validation_df.to_csv(REPORT_DIR / "opponent_validation.csv", index=False)
    (REPORT_DIR / "fixed_decisions_self_play.json").write_text(json.dumps(self_play, ensure_ascii=False, indent=2))

    grouped_summary: dict[str, Any] = {}
    for team, group in validation_df.groupby("team"):
        signatures = [
            (
                int(row["products_planned"]),
                int(row["workers"]),
                int(row["engineers"]),
                int(row["agent_total"]),
                round(float(row["marketing_total"]), 2),
            )
            for _, row in group.iterrows()
        ]
        grouped_summary[team] = {
            "style": str(group.iloc[0]["style"]),
            "all_markets_active_every_round": bool(group["all_markets_active"].all()),
            "distinct_round_signatures": len(set(signatures)),
            "final_net_assets": float(group.iloc[-1]["net_assets"]),
            "rounds": group.to_dict(orient="records"),
        }

    round_summary = {}
    for round_id, group in validation_df.groupby("round_id"):
        round_summary[round_id] = {
            "negative_net_assets_count": int((group["net_assets"] < 0).sum()),
            "min_net_assets": float(group["net_assets"].min()),
            "max_net_assets": float(group["net_assets"].max()),
            "all_markets_active": bool(group["all_markets_active"].all()),
            "management_index_min": float(group["management_index"].min()),
            "quality_index_min": float(group["quality_index"].min()),
        }

    current_team13 = sort_rounds(out_df[out_df["team"] == OUR_TEAM].copy()).reset_index(drop=True)
    team13_preserved = True
    for column in original_team13.columns:
        left = original_team13[column]
        right = current_team13[column]
        try:
            matches = ((left.fillna(0).astype(float) - right.fillna(0).astype(float)).abs() < 1e-9).all()
        except Exception:
            matches = left.astype(str).equals(right.astype(str))
        if not matches:
            team13_preserved = False
            break

    opponent_only = validation_df[validation_df["team"] != OUR_TEAM]
    summary = {
        "generated_at": "2026-04-22",
        "target": "smart fixed opponents calibrated against the fair multiplayer engine",
        "team13_preserved": bool(team13_preserved),
        "opponent_count": int(opponent_only["team"].nunique()),
        "all_markets_active_every_round": bool(opponent_only["all_markets_active"].all()),
        "round_summary": round_summary,
        "r1_opponents_nonzero_management_quality": bool(
            (
                (opponent_only["round_id"] == "r1")
                & (opponent_only["management_index"] > 0)
                & (opponent_only["quality_index"] > 0)
            ).sum()
            == int((opponent_only["round_id"] == "r1").sum())
        ),
        "teams": grouped_summary,
    }
    (REPORT_DIR / "opponent_validation.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"Read source {source_path}")
    print(f"Wrote {SMART_DECISIONS_XLSX}")
    print(f"Wrote {REPORT_DIR / 'opponent_validation.csv'}")
    print(f"Wrote {REPORT_DIR / 'opponent_validation.json'}")
    print(f"Wrote {REPORT_DIR / 'fixed_decisions_self_play.json'}")


if __name__ == "__main__":
    main()
