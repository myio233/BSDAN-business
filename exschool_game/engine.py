from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.tree import DecisionTreeRegressor

from .data_loader import (
    EXSCHOOL_DIR,
    ROUND_WORKBOOK_MAP,
    attach_lags,
    normalize_fixed_decision_mode,
    parse_fixed_round_summary,
    parse_fixed_team_decisions,
    parse_key_data,
    parse_market_report_workbooks,
    parse_round_workbook,
    parse_team13_actual,
    round_sort_key,
)
from .campaign_support import build_campaign_state, build_simulation_input, next_campaign_state, payload_for_context
from .finance import build_finance_rows, loan_limit_for_state, market_report_cost_from_decision
from .inventory import build_production_snapshot, resolve_affordable_production
from .models import CampaignSimulationInput, CampaignState, MarketDecision, SimulationInput
from .market_allocation import allocate_sales_with_gap_absorption, integer_allocate_by_weights
from .modeling import (
    WeightedBlendRegressor,
    apply_home_city_to_frame,
    augment_model_matrix_with_home_city,
    build_cpi_to_share_feature_matrix,
    fit_share_model_from_cpi,
    infer_team_home_cities,
    predict_share_from_cpi_model,
    weighted_r2_score,
)
from .report_payload import assemble_simulation_report
from .research import deterministic_uniform, patent_cost_multiplier, research_success_probability
from .workforce import (
    productivity_multiplier,
    productivity_multiplier_from_ratio,
    salary_ratio,
    smoothed_average_salary,
    workforce_plan,
)
from fit_weighted_theoretical_cpi_model import (  # type: ignore  # noqa: E402
    EPS,
    apply_stage1_residual_calibration,
    base_features,
    build_context,
    build_tree_feature_matrix,
    clean_market_table,
)


TEAM_ID = "13"
REPORT_ROUND_MAP = {
    "report4_market_reports.xlsx": "r1",
    "report4_market_reports_fixed.xlsx": "r1",
    "report3_market_reports.xlsx": "r2",
    "report3_market_reports_fixed.xlsx": "r2",
    "report2_market_reports.xlsx": "r3",
    "report2_market_reports_fixed.xlsx": "r3",
    "report1_market_reports.xlsx": "r4",
    "report1_market_reports_fixed.xlsx": "r4",
}
ROUND_WORKBOOK_MAP = {
    "r1": "round_1_team13.xlsx",
    "r2": "round_2_team13.xlsx",
    "r3": "round_3_team13.xlsx",
    "r4": "round_4_team13.xlsx",
}
DEFAULT_TAX_RATE = 0.25
MIN_PRICE = 3500.0
MAX_PRICE = 25000.0
MARKET_REPORT_SUBSCRIPTION_COST = 200_000.0
LOAN_STAGE_THRESHOLDS = (50_000_000.0, 150_000_000.0, 400_000_000.0)
ACTUAL_CPI_TRAIN_WEIGHT = 100.0
PROXY_CPI_TRAIN_WEIGHT = 0.5
CPI_RF_BLEND_WEIGHT = 0.15
MAX_HEADCOUNT = 100_000
MAX_PRODUCTS_PLANNED = 1_000_000
MAX_INVESTMENT_AMOUNT = 100_000_000.0
MAX_AGENT_CHANGE_ABS = 1_000


def _to_currency(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}¥{abs(value):,.0f}"


def _to_percent(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def _round_number(value: float) -> int:
    return int(round(float(value)))


class ExschoolSimulator:
    def __init__(self, single_player_mode: str = "high-intensity") -> None:
        self.single_player_mode = normalize_fixed_decision_mode(single_player_mode)
        self.key_data = parse_key_data()
        self.market_df = parse_market_report_workbooks()
        self.fixed_decisions_df = parse_fixed_team_decisions(self.single_player_mode)
        self.fixed_round_summary_df = parse_fixed_round_summary(self.single_player_mode)
        self.team13_actual_df = parse_team13_actual()
        self.team_home_city_map = infer_team_home_cities(self.market_df)
        self.team_ids = sorted(self.fixed_decisions_df["team"].astype(str).unique(), key=lambda value: int(value))
        self.round_levels = sorted(self.market_df["round"].dropna().unique(), key=round_sort_key)
        self.market_levels = sorted(self.market_df["market"].dropna().unique())
        self.round_contexts = {round_id: parse_round_workbook(round_id) for round_id in ROUND_WORKBOOK_MAP}
        full_markets = sorted(self.key_data["markets"].keys())
        for context in self.round_contexts.values():
            market_defaults = {market: {**context["market_defaults"].get(market, {})} for market in full_markets}
            for market in full_markets:
                baseline_price = max(float(self.key_data["markets"][market]["initial_avg_price"]), MIN_PRICE)
                if market_defaults[market]:
                    market_defaults[market].setdefault("previous_agents", 0)
                    market_defaults[market].setdefault("actual_change", 0)
                    market_defaults[market].setdefault("actual_after", market_defaults[market]["previous_agents"])
                    market_defaults[market].setdefault("actual_marketing_investment", 0.0)
                    market_defaults[market]["actual_price"] = max(
                        float(market_defaults[market].get("actual_price", baseline_price) or baseline_price),
                        MIN_PRICE,
                    )
                    market_defaults[market].setdefault("actual_sales_volume", 0.0)
                    market_defaults[market].setdefault("actual_market_share", 0.0)
                    market_defaults[market].setdefault("actual_competitive_power", 0.0)
                else:
                    market_defaults[market] = {
                        "previous_agents": 0,
                        "actual_change": 0,
                        "actual_after": 0,
                        "actual_marketing_investment": 0.0,
                        "actual_price": baseline_price,
                        "actual_sales_volume": 0.0,
                        "actual_market_share": 0.0,
                        "actual_competitive_power": 0.0,
                    }
            context["market_defaults"] = market_defaults
            context["visible_markets"] = full_markets
        self.component_storage_unit_cost, self.product_storage_unit_cost = self._infer_company_storage_unit_costs()
        self.round_salary_anchors = self._build_round_salary_anchors()
        for context in self.round_contexts.values():
            context["component_storage_unit_cost"] = self.component_storage_unit_cost
            context["product_storage_unit_cost"] = self.product_storage_unit_cost
        self.fixed_products_by_round_team = {
            ("EXSCHOOL", str(row["round_id"]), str(row["team"])): float(row["products_planned"])
            for _, row in self.fixed_decisions_df.iterrows()
            if pd.notna(row.get("products_planned"))
        }
        self.share_model, self.cpi_model = self._train_models()

    @staticmethod
    def _default_market_report_subscription(values: dict[str, Any]) -> bool:
        previous_agents = int(values.get("previous_agents", 0) or 0)
        actual_sales_volume = float(values.get("actual_sales_volume", 0.0) or 0.0)
        return previous_agents > 0 or actual_sales_volume > 0.0

    def _infer_company_storage_unit_costs(self) -> tuple[float, float]:
        round1 = self.round_contexts.get("r1")
        if round1 is not None:
            component_cost = abs(float(round1.get("finance_rows", {}).get("Components storage cost", {}).get("cash_flow") or 0.0))
            product_cost = abs(float(round1.get("finance_rows", {}).get("Products storage cost", {}).get("cash_flow") or 0.0))
            produced_products = float(round1.get("products_produced_actual", 0.0) or 0.0)
            component_units = produced_products * 7.0
            if component_units > 0 and component_cost > 0:
                component_unit = component_cost / component_units
            else:
                component_unit = min(float(market["component_storage_unit_cost"]) for market in self.key_data["markets"].values())
            if produced_products > 0 and product_cost > 0:
                product_unit = product_cost / produced_products
            else:
                product_unit = min(float(market["product_storage_unit_cost"]) for market in self.key_data["markets"].values())
            return float(component_unit), float(product_unit)
        return (
            min(float(market["component_storage_unit_cost"]) for market in self.key_data["markets"].values()),
            min(float(market["product_storage_unit_cost"]) for market in self.key_data["markets"].values()),
        )

    def _build_round_salary_anchors(self) -> dict[str, dict[str, float]]:
        anchors: dict[str, dict[str, float]] = {}
        for round_id, context in self.round_contexts.items():
            round_rows = self.fixed_decisions_df[self.fixed_decisions_df["round_id"] == round_id].copy()
            total_workers = float(round_rows["workers"].fillna(0).sum()) if not round_rows.empty else float(context["workers_actual"])
            total_engineers = float(round_rows["engineers"].fillna(0).sum()) if not round_rows.empty else float(context["engineers_actual"])
            baseline_worker_avg = float(context.get("worker_avg_salary_actual") or context.get("worker_salary_actual") or 0.0)
            baseline_engineer_avg = float(context.get("engineer_avg_salary_actual") or context.get("engineer_salary_actual") or 0.0)
            our_workers = float(context.get("workers_actual", 0.0) or 0.0)
            our_engineers = float(context.get("engineers_actual", 0.0) or 0.0)
            our_worker_salary = float(context.get("worker_salary_actual", baseline_worker_avg) or baseline_worker_avg)
            our_engineer_salary = float(context.get("engineer_salary_actual", baseline_engineer_avg) or baseline_engineer_avg)
            peer_workers = max(total_workers - our_workers, 0.0)
            peer_engineers = max(total_engineers - our_engineers, 0.0)
            peer_worker_avg = (
                ((baseline_worker_avg * total_workers) - (our_workers * our_worker_salary)) / peer_workers
                if peer_workers > 0
                else baseline_worker_avg
            )
            peer_engineer_avg = (
                ((baseline_engineer_avg * total_engineers) - (our_engineers * our_engineer_salary)) / peer_engineers
                if peer_engineers > 0
                else baseline_engineer_avg
            )
            anchors[round_id] = {
                "worker_avg_salary": baseline_worker_avg,
                "engineer_avg_salary": baseline_engineer_avg,
                "peer_workers": peer_workers,
                "peer_engineers": peer_engineers,
                "peer_worker_payroll": max(peer_worker_avg, 0.0) * peer_workers,
                "peer_engineer_payroll": max(peer_engineer_avg, 0.0) * peer_engineers,
            }
        return anchors

    def _infer_team_home_cities(self) -> dict[str, str]:
        return infer_team_home_cities(self.market_df)

    def _apply_home_city_to_frame(
        self,
        df: pd.DataFrame,
        current_home_city: str | None = None,
        *,
        home_city_overrides: dict[str, str] | None = None,
    ) -> pd.DataFrame:
        return apply_home_city_to_frame(
            df,
            self.team_home_city_map,
            team_id=TEAM_ID,
            current_home_city=current_home_city,
            home_city_overrides=home_city_overrides,
        )

    @staticmethod
    def _augment_model_matrix_with_home_city(X: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
        return augment_model_matrix_with_home_city(X, df)

    def _training_frame(self) -> pd.DataFrame:
        merged = self.market_df.merge(self.team13_actual_df, on=["round", "market", "team"], how="left")
        merged = attach_lags(merged)
        merged = clean_market_table(merged)
        merged = self._apply_home_city_to_frame(merged)
        return merged

    def _market_templates_for_round(self, round_id: str, visible_markets: list[str]) -> dict[str, dict[str, Any]]:
        round_rows = self.market_df[self.market_df["round"] == round_id].copy()
        templates: dict[str, dict[str, Any]] = {}
        for market in visible_markets:
            market_rows = round_rows[round_rows["market"] == market]
            if market_rows.empty:
                continue
            templates[market] = market_rows.iloc[0].to_dict()
        return templates

    def _home_city_for_team(self, team: str, current_home_city: str | None = None) -> str:
        if current_home_city:
            return str(current_home_city)
        return str(self.team_home_city_map.get(team, "Shanghai"))

    def _fixed_round_summary_row(self, round_id: str, team: str) -> pd.Series | None:
        if self.fixed_round_summary_df.empty:
            return None
        rows = self.fixed_round_summary_df[
            (self.fixed_round_summary_df["round_id"] == round_id) & (self.fixed_round_summary_df["team"].astype(str) == str(team))
        ]
        if rows.empty:
            return None
        return rows.iloc[0]

    def _initial_company_state(
        self,
        team: str,
        current_home_city: str | None = None,
        *,
        preserve_real_original_round1: bool = True,
    ) -> CampaignState:
        home_city = self._home_city_for_team(team, current_home_city)
        home_market = self.key_data["markets"].get(home_city) or next(iter(self.key_data["markets"].values()))
        initial_worker_avg, initial_engineer_avg = self._initial_global_average_salary()
        if self.single_player_mode == "real-original" and preserve_real_original_round1:
            summary_row = self._fixed_round_summary_row("r1", team)
            decision_row = self.fixed_decisions_df[
                (self.fixed_decisions_df["round_id"] == "r1") & (self.fixed_decisions_df["team"].astype(str) == str(team))
            ]
            if summary_row is not None and not decision_row.empty:
                row = decision_row.iloc[0]
                market_agents_before = {
                    market: int(row.get(f"{market.lower()}_agents_before", 0) or 0) for market in self.key_data["markets"].keys()
                }
                return CampaignState(
                    current_cash=float(summary_row.get("starting_cash_est", 0.0) or 0.0),
                    current_debt=float(summary_row.get("starting_debt_est", 0.0) or 0.0),
                    workers=int(summary_row.get("workers_est", 0) or 0),
                    engineers=int(summary_row.get("engineers_est", 0) or 0),
                    worker_salary=float(summary_row.get("worker_salary_est", home_market.get("initial_worker_salary", initial_worker_avg)) or 0.0),
                    engineer_salary=float(summary_row.get("engineer_salary_est", home_market.get("initial_engineer_salary", initial_engineer_avg)) or 0.0),
                    market_agents_after=market_agents_before,
                    previous_management_index=0.0,
                    previous_quality_index=0.0,
                    worker_avg_salary=float(initial_worker_avg),
                    engineer_avg_salary=float(initial_engineer_avg),
                    worker_recent=0,
                    worker_mature=0,
                    worker_experienced=int(summary_row.get("workers_est", 0) or 0),
                    engineer_recent=0,
                    engineer_mature=0,
                    engineer_experienced=int(summary_row.get("engineers_est", 0) or 0),
                    component_capacity=0.0,
                    product_capacity=0.0,
                    component_inventory=0.0,
                    product_inventory=0.0,
                    active_patents=0,
                    accumulated_research_investment=0.0,
                    last_round_id=None,
                )
        round1 = self.round_contexts["r1"]
        return CampaignState(
            current_cash=float(round1["starting_cash"]),
            current_debt=float(round1["starting_debt"]),
            workers=0,
            engineers=0,
            worker_salary=float(home_market.get("initial_worker_salary", initial_worker_avg)),
            engineer_salary=float(home_market.get("initial_engineer_salary", initial_engineer_avg)),
            market_agents_after={market: 0 for market in self.key_data["markets"].keys()},
            previous_management_index=0.0,
            previous_quality_index=0.0,
            worker_avg_salary=float(initial_worker_avg),
            engineer_avg_salary=float(initial_engineer_avg),
            worker_recent=0,
            worker_mature=0,
            worker_experienced=0,
            engineer_recent=0,
            engineer_mature=0,
            engineer_experienced=0,
            component_capacity=0.0,
            product_capacity=0.0,
            component_inventory=0.0,
            product_inventory=0.0,
            active_patents=0,
            accumulated_research_investment=0.0,
            last_round_id=None,
        )

    def _context_for_company_state(
        self,
        round_id: str,
        team: str,
        state: CampaignState | None,
        *,
        current_home_city: str | None = None,
        game_id: str | None = None,
        use_historical_initial_state: bool = True,
    ) -> dict[str, Any]:
        base = self.round_contexts[round_id]
        effective_state = state if state is not None else self._initial_company_state(
            team,
            current_home_city,
            preserve_real_original_round1=use_historical_initial_state,
        )
        home_city = self._home_city_for_team(team, current_home_city)
        market_defaults: dict[str, dict[str, Any]] = {}
        for market, values in base["market_defaults"].items():
            previous_agents = int(effective_state.market_agents_after.get(market, 0))
            baseline_price = max(float(self.key_data["markets"][market]["initial_avg_price"]), MIN_PRICE)
            market_defaults[market] = {
                **values,
                "previous_agents": previous_agents,
                "actual_change": 0,
                "actual_after": previous_agents,
                "actual_marketing_investment": 0.0,
                "actual_price": baseline_price,
                "actual_sales_volume": 0.0,
                "actual_market_share": 0.0,
                "actual_competitive_power": 0.0,
                "payload_agent_change": 0,
                "payload_marketing_investment": 0.0,
                "payload_price": baseline_price,
            }
        context = {
            **base,
            "team_id": str(team),
            "game_id": str(game_id or "default"),
            "starting_cash": float(effective_state.current_cash),
            "starting_debt": float(effective_state.current_debt),
            "workers_actual": int(effective_state.workers),
            "engineers_actual": int(effective_state.engineers),
            "worker_salary_actual": float(effective_state.worker_salary),
            "engineer_salary_actual": float(effective_state.engineer_salary),
            "market_defaults": market_defaults,
            "campaign_previous_management_index": float(effective_state.previous_management_index),
            "campaign_previous_quality_index": float(effective_state.previous_quality_index),
            "campaign_last_round_id": effective_state.last_round_id,
            "worker_avg_salary_prev": float(effective_state.worker_avg_salary),
            "engineer_avg_salary_prev": float(effective_state.engineer_avg_salary),
            "worker_recent_prev": int(effective_state.worker_recent),
            "worker_mature_prev": int(effective_state.worker_mature),
            "worker_experienced_prev": int(effective_state.worker_experienced),
            "engineer_recent_prev": int(effective_state.engineer_recent),
            "engineer_mature_prev": int(effective_state.engineer_mature),
            "engineer_experienced_prev": int(effective_state.engineer_experienced),
            "component_capacity_prev": float(effective_state.component_capacity),
            "product_capacity_prev": float(effective_state.product_capacity),
            "component_inventory_prev": float(effective_state.component_inventory),
            "product_inventory_prev": float(effective_state.product_inventory),
            "active_patents_prev": int(effective_state.active_patents),
            "accumulated_research_investment_prev": float(effective_state.accumulated_research_investment),
            "current_home_city": str(current_home_city or ""),
        }
        context["loan_limit"] = self._loan_limit_for_state(
            float(context["starting_cash"]),
            float(context["starting_debt"]),
            home_city,
        )
        return self._apply_home_city_to_context(context, home_city)

    def _apply_home_city_to_context(self, context: dict[str, Any], home_city: str | None) -> dict[str, Any]:
        city = str(home_city or "Shanghai")
        city_data = self.key_data["markets"].get(city) or next(iter(self.key_data["markets"].values()))
        context["current_home_city"] = city
        context["interest_rate"] = float(city_data.get("interest_rate", context.get("interest_rate", 0.03)) or 0.03)
        context["component_material_price"] = float(
            city_data.get("component_material_unit_cost", context.get("component_material_price", 188.0)) or 188.0
        )
        context["product_material_price"] = float(
            city_data.get("product_material_unit_cost", context.get("product_material_price", 540.0)) or 540.0
        )
        context["component_storage_unit_cost"] = float(
            city_data.get("component_storage_unit_cost", context.get("component_storage_unit_cost", self.component_storage_unit_cost))
            or self.component_storage_unit_cost
        )
        context["product_storage_unit_cost"] = float(
            city_data.get("product_storage_unit_cost", context.get("product_storage_unit_cost", self.product_storage_unit_cost))
            or self.product_storage_unit_cost
        )
        context["loan_limit"] = self._loan_limit_for_state(
            float(context.get("starting_cash", 0.0) or 0.0),
            float(context.get("starting_debt", 0.0) or 0.0),
            city,
        )
        if "payload_loan_delta" in context and self.single_player_mode != "real-original":
            context["payload_loan_delta"] = self._clamp_loan_delta(float(context.get("payload_loan_delta", 0.0) or 0.0), context)
        return context

    def _campaign_state_from_context(self, context: dict[str, Any]) -> CampaignState:
        return CampaignState(
            current_cash=float(context.get("starting_cash", 0.0) or 0.0),
            current_debt=float(context.get("starting_debt", 0.0) or 0.0),
            workers=int(context.get("workers_actual", 0) or 0),
            engineers=int(context.get("engineers_actual", 0) or 0),
            worker_salary=float(context.get("worker_salary_actual", 0.0) or 0.0),
            engineer_salary=float(context.get("engineer_salary_actual", 0.0) or 0.0),
            market_agents_after={
                str(market): int(values.get("previous_agents", 0) or 0)
                for market, values in dict(context.get("market_defaults", {})).items()
                if isinstance(values, dict)
            },
            previous_management_index=float(context.get("campaign_previous_management_index", 0.0) or 0.0),
            previous_quality_index=float(context.get("campaign_previous_quality_index", 0.0) or 0.0),
            worker_avg_salary=float(context.get("worker_avg_salary_prev", context.get("worker_salary_actual", 0.0)) or 0.0),
            engineer_avg_salary=float(context.get("engineer_avg_salary_prev", context.get("engineer_salary_actual", 0.0)) or 0.0),
            worker_recent=int(context.get("worker_recent_prev", context.get("workers_actual", 0)) or 0),
            worker_mature=int(context.get("worker_mature_prev", 0) or 0),
            worker_experienced=int(context.get("worker_experienced_prev", 0) or 0),
            engineer_recent=int(context.get("engineer_recent_prev", context.get("engineers_actual", 0)) or 0),
            engineer_mature=int(context.get("engineer_mature_prev", 0) or 0),
            engineer_experienced=int(context.get("engineer_experienced_prev", 0) or 0),
            component_capacity=float(context.get("component_capacity_prev", 0.0) or 0.0),
            product_capacity=float(context.get("product_capacity_prev", 0.0) or 0.0),
            component_inventory=float(context.get("component_inventory_prev", 0.0) or 0.0),
            product_inventory=float(context.get("product_inventory_prev", 0.0) or 0.0),
            active_patents=int(context.get("active_patents_prev", 0) or 0),
            accumulated_research_investment=float(context.get("accumulated_research_investment_prev", 0.0) or 0.0),
            last_round_id=str(context.get("campaign_last_round_id") or "") or None,
        )

    @staticmethod
    def _max_repayment_for_state(starting_cash: float, starting_debt: float) -> float:
        return max(min(float(starting_cash), float(starting_debt)), 0.0)

    def _clamp_loan_delta(self, loan_delta: float, context: dict[str, Any]) -> float:
        upper = float(context.get("loan_limit", loan_delta) or 0.0)
        lower = -self._max_repayment_for_state(
            float(context.get("starting_cash", 0.0) or 0.0),
            float(context.get("starting_debt", 0.0) or 0.0),
        )
        return max(min(float(loan_delta), upper), lower)

    def _previous_global_average_salary(self, team_states: dict[str, CampaignState | None]) -> tuple[float, float]:
        populated = [state for state in team_states.values() if state is not None]
        if not populated:
            return self._initial_global_average_salary()
        worker_payroll = 0.0
        worker_total = 0.0
        engineer_payroll = 0.0
        engineer_total = 0.0
        for state in populated:
            worker_avg = float(state.worker_avg_salary)
            engineer_avg = float(state.engineer_avg_salary)
            workers = max(float(state.workers), 0.0)
            engineers = max(float(state.engineers), 0.0)
            if worker_avg > 0 and workers > 0:
                worker_payroll += worker_avg * workers
                worker_total += workers
            if engineer_avg > 0 and engineers > 0:
                engineer_payroll += engineer_avg * engineers
                engineer_total += engineers
        if worker_total > 0 and engineer_total > 0:
            return worker_payroll / worker_total, engineer_payroll / engineer_total
        return self._initial_global_average_salary()

    def _current_global_average_salary_for_multiplayer(
        self,
        decisions_by_team: dict[str, SimulationInput],
        previous_worker_avg: float,
        previous_engineer_avg: float,
    ) -> tuple[float, float]:
        worker_total = sum(float(decision.workers) for decision in decisions_by_team.values())
        engineer_total = sum(float(decision.engineers) for decision in decisions_by_team.values())
        worker_weight = sum(float(decision.workers) * float(decision.worker_salary) for decision in decisions_by_team.values())
        engineer_weight = sum(float(decision.engineers) * float(decision.engineer_salary) for decision in decisions_by_team.values())
        current_worker_avg = worker_weight / worker_total if worker_total > 0 else previous_worker_avg
        current_engineer_avg = engineer_weight / engineer_total if engineer_total > 0 else previous_engineer_avg
        return (
            smoothed_average_salary(current_worker_avg, previous_worker_avg),
            smoothed_average_salary(current_engineer_avg, previous_engineer_avg),
        )

    def _round_decisions(self, round_id: str, team13_decision: SimulationInput | None = None) -> dict[str, SimulationInput]:
        decisions: dict[str, SimulationInput] = {}
        for team in self.team_ids:
            if team == TEAM_ID and team13_decision is not None and team13_decision.round_id == round_id:
                decisions[team] = team13_decision
                continue
            fixed = self._fixed_decision_for_team(round_id, team)
            if fixed is not None:
                decisions[team] = fixed
        return decisions

    def _round_decisions_with_overrides(
        self,
        round_id: str,
        *,
        decision_overrides_by_team: dict[str, SimulationInput] | None = None,
    ) -> dict[str, SimulationInput]:
        decisions = self._round_decisions(round_id)
        for team, decision in (decision_overrides_by_team or {}).items():
            normalized_team = str(team).strip()
            if normalized_team and decision.round_id == round_id:
                decisions[normalized_team] = decision
        return decisions

    @staticmethod
    def _weighted_r2_score(actual: np.ndarray, pred: np.ndarray, sample_weight: np.ndarray) -> float:
        return weighted_r2_score(actual, pred, sample_weight)

    def _build_cpi_to_share_feature_matrix(self, df: pd.DataFrame, predicted_cpi: np.ndarray) -> pd.DataFrame:
        fixed_products_team_filter = None if getattr(self, "single_player_mode", "") == "real-original" else {TEAM_ID}
        return build_cpi_to_share_feature_matrix(
            df,
            predicted_cpi,
            fixed_products_by_round_team=getattr(self, "fixed_products_by_round_team", None),
            fixed_products_team_filter=fixed_products_team_filter,
        )

    def _fit_share_model_from_cpi(
        self,
        train: pd.DataFrame,
        cpi_pred: np.ndarray,
    ) -> dict[str, Any]:
        return fit_share_model_from_cpi(
            train,
            cpi_pred,
            team_id=TEAM_ID,
            fixed_products_by_round_team=getattr(self, "fixed_products_by_round_team", None),
        )

    @staticmethod
    def _predict_runtime_cpi_from_log_predictions(
        base_pred_log: np.ndarray,
        markets: pd.Series | np.ndarray,
        prices: pd.Series | np.ndarray,
        *,
        rounds: pd.Series | np.ndarray | None = None,
        prev_marketshare_clean: pd.Series | np.ndarray | None = None,
        prev_market_utilization_clean: pd.Series | np.ndarray | None = None,
        apply_residual_calibrator: bool = True,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        base_pred_log_arr = np.asarray(base_pred_log, dtype=float)
        if apply_residual_calibrator:
            pred_log, residual_log_shift = apply_stage1_residual_calibration(
                base_pred_log_arr,
                markets,
                rounds=rounds,
                prev_marketshare_clean=prev_marketshare_clean,
                prev_market_utilization_clean=prev_market_utilization_clean,
            )
        else:
            pred_log = base_pred_log_arr
            residual_log_shift = np.zeros(len(base_pred_log_arr), dtype=float)

        cpi_pred = np.maximum(np.exp(pred_log) - EPS, 0.0)
        price_array = np.asarray(prices, dtype=float)
        price_penalty_start = 0.98 * MAX_PRICE
        penalty_ratio = np.clip((price_array - price_penalty_start) / max(MAX_PRICE - price_penalty_start, 1.0), 0.0, 1.0)
        price_penalty_divisor = 1.0 + 14.0 * penalty_ratio
        cpi_pred = np.where(price_array > price_penalty_start, cpi_pred / price_penalty_divisor, cpi_pred)
        return pred_log, residual_log_shift, cpi_pred

    def _train_models(self) -> tuple[dict[str, Any], dict[str, Any]]:
        train = self._training_frame()
        feats = base_features(train)
        context = build_context(feats, self.round_levels, self.market_levels)
        X = build_tree_feature_matrix(feats, context).fillna(0.0)
        X = self._augment_model_matrix_with_home_city(X, train)

        cpi_target = np.where(train["actual_real_cpi"].notna(), train["actual_real_cpi"], train["marketshare_clean"])
        cpi_weight = np.where(train["actual_real_cpi"].notna(), ACTUAL_CPI_TRAIN_WEIGHT, PROXY_CPI_TRAIN_WEIGHT)
        cpi_gbr_model = GradientBoostingRegressor(
            loss="squared_error",
            n_estimators=250,
            learning_rate=0.05,
            max_depth=4,
            min_samples_leaf=1,
            subsample=0.7,
            random_state=42,
        )
        cpi_rf_model = RandomForestRegressor(
            n_estimators=600,
            max_depth=None,
            min_samples_leaf=1,
            random_state=42,
            n_jobs=1,
        )
        log_cpi_target = np.log(np.maximum(cpi_target, EPS))
        cpi_gbr_model.fit(X, log_cpi_target, sample_weight=cpi_weight)
        cpi_rf_model.fit(X, log_cpi_target, sample_weight=cpi_weight)
        cpi_model = WeightedBlendRegressor(
            [
                (1.0 - CPI_RF_BLEND_WEIGHT, cpi_gbr_model),
                (CPI_RF_BLEND_WEIGHT, cpi_rf_model),
            ]
        )
        cpi_pred = np.maximum(np.exp(cpi_model.predict(X)) - EPS, 0.0)
        share_model = self._fit_share_model_from_cpi(train=feats, cpi_pred=cpi_pred)

        return (
            share_model,
            {
                "estimator": cpi_model,
                "columns": X.columns.tolist(),
                "rf_blend_weight": CPI_RF_BLEND_WEIGHT,
            },
        )

    def available_rounds(self) -> list[str]:
        return sorted(self.round_contexts.keys(), key=round_sort_key)

    def _build_simulation_input(
        self,
        round_id: str,
        raw: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
        headcount_is_delta: bool = False,
    ) -> SimulationInput:
        return build_simulation_input(
            visible_markets=set(self.round_contexts[round_id]["visible_markets"]),
            round_id=round_id,
            raw=raw,
            context=context,
            headcount_is_delta=headcount_is_delta,
        )

    def _payload_for_context(self, round_id: str, context: dict[str, Any]) -> dict[str, Any]:
        return payload_for_context(round_id, context)

    def default_payload(self, round_id: str) -> dict[str, Any]:
        return self._payload_for_context(round_id, self.round_contexts[round_id])

    def stateful_default_payload(
        self,
        round_id: str,
        state: CampaignState | None,
        *,
        current_home_city: str | None = None,
    ) -> dict[str, Any]:
        if self.single_player_mode == "real-original":
            fixed = self._fixed_decision_for_team(round_id, TEAM_ID)
            if fixed is not None:
                previous_workers = int(state.workers) if state is not None else 0
                previous_engineers = int(state.engineers) if state is not None else 0
                return {
                    "round_id": round_id,
                    "loan_delta": float(fixed.loan_delta),
                    "workers": int(fixed.workers) - previous_workers,
                    "engineers": int(fixed.engineers) - previous_engineers,
                    "worker_salary": float(fixed.worker_salary),
                    "engineer_salary": float(fixed.engineer_salary),
                    "management_investment": float(fixed.management_investment),
                    "quality_investment": float(fixed.quality_investment),
                    "research_investment": float(fixed.research_investment),
                    "products_planned": int(fixed.products_planned),
                    "markets": {
                        market: {
                            "agent_change": int(item.agent_change),
                            "marketing_investment": float(item.marketing_investment),
                            "price": float(item.price)
                            if float(item.price) > 0
                            else max(float(self.key_data["markets"][market]["initial_avg_price"]), MIN_PRICE),
                            "subscribed_market_report": bool(item.subscribed_market_report),
                        }
                        for market, item in fixed.market_decisions.items()
                    },
                }
        return self._payload_for_context(
            round_id,
            self._context_with_campaign_state(round_id, state, current_home_city=current_home_city),
        )

    def campaign_default_payload(self) -> dict[str, Any]:
        return {"rounds": {round_id: self.default_payload(round_id) for round_id in self.available_rounds()}}

    def payload_from_json(self, payload: str) -> SimulationInput:
        raw = json.loads(payload)
        round_id = str(raw["round_id"])
        return self._build_simulation_input(round_id, raw)

    def campaign_payload_from_json(self, payload: str) -> CampaignSimulationInput:
        raw = json.loads(payload)
        rounds = {
            round_id: self._build_simulation_input(round_id, raw["rounds"][round_id])
            for round_id in self.available_rounds()
            if round_id in raw.get("rounds", {})
        }
        return CampaignSimulationInput(rounds=rounds)

    def parse_form(self, form: dict[str, Any], context: dict[str, Any] | None = None) -> SimulationInput:
        round_id = str(form["round_id"])
        market_decisions = {}
        for market in self.round_contexts[round_id]["visible_markets"]:
            slug = market.lower()
            market_decisions[market] = MarketDecision(
                agent_change=int(form[f"{slug}_agent_change"]),
                marketing_investment=float(form[f"{slug}_marketing_investment"]),
                price=float(form[f"{slug}_price"]),
                subscribed_market_report=form.get(f"{slug}_market_report") == "1",
            )
        previous_workers = int((context or {}).get("workers_actual", 0) or 0)
        previous_engineers = int((context or {}).get("engineers_actual", 0) or 0)
        return SimulationInput(
            round_id=round_id,
            loan_delta=float(form["loan_delta"]),
            workers=max(previous_workers + int(form["workers"]), 0),
            engineers=max(previous_engineers + int(form["engineers"]), 0),
            worker_salary=float(form["worker_salary"]),
            engineer_salary=float(form["engineer_salary"]),
            management_investment=float(form["management_investment"]),
            quality_investment=float(form["quality_investment"]),
            research_investment=float(form.get("research_investment", 0.0)),
            products_planned=int(form["products_planned"]),
            market_decisions=market_decisions,
        )

    def parse_campaign_form(self, form: dict[str, Any]) -> CampaignSimulationInput:
        rounds = {}
        for round_id in self.available_rounds():
            prefix = f"{round_id}_"
            market_decisions = {}
            for market in self.round_contexts[round_id]["visible_markets"]:
                slug = market.lower()
                market_decisions[market] = MarketDecision(
                    agent_change=int(form[f"{prefix}{slug}_agent_change"]),
                    marketing_investment=float(form[f"{prefix}{slug}_marketing_investment"]),
                    price=float(form[f"{prefix}{slug}_price"]),
                    subscribed_market_report=form.get(f"{prefix}{slug}_market_report") == "1",
                )
            rounds[round_id] = SimulationInput(
                round_id=round_id,
                loan_delta=float(form[f"{prefix}loan_delta"]),
                workers=int(form[f"{prefix}workers"]),
                engineers=int(form[f"{prefix}engineers"]),
                worker_salary=float(form[f"{prefix}worker_salary"]),
                engineer_salary=float(form[f"{prefix}engineer_salary"]),
                management_investment=float(form[f"{prefix}management_investment"]),
                quality_investment=float(form[f"{prefix}quality_investment"]),
                research_investment=float(form.get(f"{prefix}research_investment", 0.0)),
                products_planned=int(form[f"{prefix}products_planned"]),
                market_decisions=market_decisions,
            )
        return CampaignSimulationInput(rounds=rounds)

    def _build_campaign_state(self, round_id: str) -> CampaignState:
        context = self.round_contexts[round_id]
        initial_worker_avg, initial_engineer_avg = self._initial_global_average_salary()
        return build_campaign_state(
            context=context,
            initial_worker_avg=initial_worker_avg,
            initial_engineer_avg=initial_engineer_avg,
        )

    def _context_with_campaign_state(
        self,
        round_id: str,
        state: CampaignState | None,
        current_home_city: str | None = None,
    ) -> dict[str, Any]:
        base = self.round_contexts[round_id]
        home_city = str(current_home_city or "").strip()
        if state is None:
            initial_worker_avg, initial_engineer_avg = self._initial_global_average_salary()
            market_defaults = {}
            for market, values in base["market_defaults"].items():
                baseline_price = max(float(values.get("actual_price", 0.0) or 0.0), MIN_PRICE)
                market_defaults[market] = {
                    **values,
                    "payload_agent_change": int(values.get("actual_change", 0) or 0),
                    "payload_marketing_investment": float(values.get("actual_marketing_investment", 0.0) or 0.0),
                    "payload_price": baseline_price,
                }
            context = {
                **base,
                "market_defaults": market_defaults,
                "workers_actual": int(base.get("workers_actual", 0) or 0) if self.single_player_mode == "real-original" else 0,
                "engineers_actual": int(base.get("engineers_actual", 0) or 0) if self.single_player_mode == "real-original" else 0,
                "worker_salary_actual": float(base["worker_salary_actual"]),
                "engineer_salary_actual": float(base["engineer_salary_actual"]),
                "worker_avg_salary_prev": float(initial_worker_avg),
                "engineer_avg_salary_prev": float(initial_engineer_avg),
                "worker_recent_prev": 0,
                "worker_mature_prev": 0,
                "worker_experienced_prev": int(base.get("workers_actual", 0) or 0) if self.single_player_mode == "real-original" else 0,
                "engineer_recent_prev": 0,
                "engineer_mature_prev": 0,
                "engineer_experienced_prev": int(base.get("engineers_actual", 0) or 0) if self.single_player_mode == "real-original" else 0,
                "component_capacity_prev": 0.0,
                "product_capacity_prev": 0.0,
                "component_inventory_prev": 0.0,
                "product_inventory_prev": 0.0,
                "active_patents_prev": 0,
                "accumulated_research_investment_prev": 0.0,
                "payload_loan_delta": float(base.get("actual_loan_delta", 0.0) or 0.0),
                "payload_workers": int(base.get("workers_actual", 0) or 0),
                "payload_engineers": int(base.get("engineers_actual", 0) or 0),
                "payload_worker_salary": float(base.get("worker_salary_actual", 0.0) or 0.0),
                "payload_engineer_salary": float(base.get("engineer_salary_actual", 0.0) or 0.0),
                "payload_management_investment": float(base.get("management_investment_actual", 0.0) or 0.0),
                "payload_quality_investment": float(base.get("quality_investment_actual", 0.0) or 0.0),
                "payload_research_investment": float(base.get("research_investment", 0.0) or 0.0),
                "payload_products_planned": int(base.get("products_produced_actual", 0) or 0),
            }
            context["loan_limit"] = self._loan_limit_for_state(
                float(context["starting_cash"]),
                float(context["starting_debt"]),
                str(home_city) if home_city else None,
            )
            if self.single_player_mode != "real-original":
                context["payload_loan_delta"] = self._clamp_loan_delta(
                    float(context.get("payload_loan_delta", context["actual_loan_delta"]) or 0.0),
                    context,
                )
            return self._apply_home_city_to_context(context, home_city) if home_city else context

        market_defaults = {}
        for market, values in base["market_defaults"].items():
            previous_agents = int(state.market_agents_after.get(market, values["previous_agents"]))
            baseline_price = max(float(values.get("actual_price", 0.0) or 0.0), MIN_PRICE)
            market_defaults[market] = {
                **values,
                "previous_agents": previous_agents,
                "actual_after": previous_agents,
                "payload_agent_change": int(values.get("actual_change", 0) or 0),
                "payload_marketing_investment": float(values.get("actual_marketing_investment", 0.0) or 0.0),
                "payload_price": baseline_price,
            }

        context = {
            **base,
            "starting_cash": state.current_cash,
            "starting_debt": state.current_debt,
            "workers_actual": state.workers,
            "engineers_actual": state.engineers,
            "worker_salary_actual": state.worker_salary,
            "engineer_salary_actual": state.engineer_salary,
            "market_defaults": market_defaults,
            "campaign_previous_management_index": state.previous_management_index,
            "campaign_previous_quality_index": state.previous_quality_index,
            "campaign_last_round_id": state.last_round_id,
            "worker_avg_salary_prev": state.worker_avg_salary,
            "engineer_avg_salary_prev": state.engineer_avg_salary,
            "worker_recent_prev": state.worker_recent,
            "worker_mature_prev": state.worker_mature,
            "worker_experienced_prev": state.worker_experienced,
            "engineer_recent_prev": state.engineer_recent,
            "engineer_mature_prev": state.engineer_mature,
            "engineer_experienced_prev": state.engineer_experienced,
            "component_capacity_prev": state.component_capacity,
            "product_capacity_prev": state.product_capacity,
            "component_inventory_prev": state.component_inventory,
            "product_inventory_prev": state.product_inventory,
            "active_patents_prev": state.active_patents,
            "accumulated_research_investment_prev": state.accumulated_research_investment,
            "payload_loan_delta": float(base.get("actual_loan_delta", 0.0) or 0.0),
            "payload_workers": int(base.get("workers_actual", 0) or 0),
            "payload_engineers": int(base.get("engineers_actual", 0) or 0),
            "payload_worker_salary": float(state.worker_salary),
            "payload_engineer_salary": float(state.engineer_salary),
            "payload_management_investment": float(base.get("management_investment_actual", 0.0) or 0.0),
            "payload_quality_investment": float(base.get("quality_investment_actual", 0.0) or 0.0),
            "payload_research_investment": float(base.get("research_investment", 0.0) or 0.0),
            "payload_products_planned": int(base.get("products_produced_actual", 0) or 0),
        }
        context["loan_limit"] = self._loan_limit_for_state(
            float(context["starting_cash"]),
            float(context["starting_debt"]),
            home_city or None,
        )
        if self.single_player_mode != "real-original":
            context["payload_loan_delta"] = self._clamp_loan_delta(float(context.get("payload_loan_delta", 0.0) or 0.0), context)
        return self._apply_home_city_to_context(context, home_city) if home_city else context

    def _initial_global_average_salary(self) -> tuple[float, float]:
        round1_context = self.round_contexts.get("r1")
        if round1_context is not None:
            worker_avg = float(round1_context.get("worker_avg_salary_actual") or 0.0)
            engineer_avg = float(round1_context.get("engineer_avg_salary_actual") or 0.0)
            if worker_avg > 0 and engineer_avg > 0:
                return worker_avg, engineer_avg
        round1 = self.fixed_decisions_df[self.fixed_decisions_df["round_id"] == "r1"].copy()
        if round1.empty:
            default_market = self.key_data["markets"].get("Shanghai") or next(iter(self.key_data["markets"].values()))
            return (
                float(default_market.get("initial_worker_salary", 2500.0)),
                float(default_market.get("initial_engineer_salary", 4700.0)),
            )
        worker_weight = 0.0
        worker_total = 0.0
        engineer_weight = 0.0
        engineer_total = 0.0
        for _, row in round1.iterrows():
            team = str(row["team"])
            home = self.team_home_city_map.get(team, "Shanghai")
            market = self.key_data["markets"].get(home) or next(iter(self.key_data["markets"].values()))
            workers = float(row.get("workers", 0) or 0)
            engineers = float(row.get("engineers", 0) or 0)
            worker_weight += workers * float(market.get("initial_worker_salary", row.get("worker_salary", 2500.0) or 2500.0))
            worker_total += workers
            engineer_weight += engineers * float(market.get("initial_engineer_salary", row.get("engineer_salary", 4700.0) or 4700.0))
            engineer_total += engineers
        default_market = self.key_data["markets"].get("Shanghai") or next(iter(self.key_data["markets"].values()))
        worker_avg = worker_weight / worker_total if worker_total > 0 else float(default_market.get("initial_worker_salary", 2500.0))
        engineer_avg = engineer_weight / engineer_total if engineer_total > 0 else float(default_market.get("initial_engineer_salary", 4700.0))
        return worker_avg, engineer_avg

    def _current_global_average_salary(self, round_id: str, our_decision: SimulationInput, previous_worker_avg: float, previous_engineer_avg: float) -> tuple[float, float]:
        anchor = self.round_salary_anchors.get(round_id)
        if anchor is not None:
            worker_total = float(anchor["peer_workers"]) + float(our_decision.workers)
            engineer_total = float(anchor["peer_engineers"]) + float(our_decision.engineers)
            worker_weight = float(anchor["peer_worker_payroll"]) + float(our_decision.workers) * float(our_decision.worker_salary)
            engineer_weight = float(anchor["peer_engineer_payroll"]) + float(our_decision.engineers) * float(our_decision.engineer_salary)
            current_worker_avg = worker_weight / worker_total if worker_total > 0 else float(anchor["worker_avg_salary"])
            current_engineer_avg = engineer_weight / engineer_total if engineer_total > 0 else float(anchor["engineer_avg_salary"])
            return (
                smoothed_average_salary(current_worker_avg, previous_worker_avg),
                smoothed_average_salary(current_engineer_avg, previous_engineer_avg),
            )
        decisions = self.fixed_decisions_df[self.fixed_decisions_df["round_id"] == round_id].copy()
        decisions = decisions[decisions["team"].astype(str) != TEAM_ID]
        worker_weight = float(our_decision.workers) * float(our_decision.worker_salary)
        worker_total = float(our_decision.workers)
        engineer_weight = float(our_decision.engineers) * float(our_decision.engineer_salary)
        engineer_total = float(our_decision.engineers)
        for _, row in decisions.iterrows():
            worker_weight += float(row.get("workers", 0) or 0) * float(row.get("worker_salary", 0.0) or 0.0)
            worker_total += float(row.get("workers", 0) or 0)
            engineer_weight += float(row.get("engineers", 0) or 0) * float(row.get("engineer_salary", 0.0) or 0.0)
            engineer_total += float(row.get("engineers", 0) or 0)
        current_worker_avg = worker_weight / worker_total if worker_total > 0 else previous_worker_avg
        current_engineer_avg = engineer_weight / engineer_total if engineer_total > 0 else previous_engineer_avg
        return (
            smoothed_average_salary(current_worker_avg, previous_worker_avg),
            smoothed_average_salary(current_engineer_avg, previous_engineer_avg),
        )

    def _capacity_details(
        self,
        *,
        round_id: str,
        worker_plan: dict[str, Any],
        engineer_plan: dict[str, Any],
        worker_salary: float,
        engineer_salary: float,
        benchmark_worker_avg: float,
        benchmark_engineer_avg: float,
    ) -> dict[str, dict[str, float]]:
        round_context = self.round_contexts[round_id]
        worker_reference_productivity = float(round_context.get("components_productivity", 24.0) or 24.0)
        engineer_reference_productivity = float(round_context.get("products_productivity", 9.0) or 9.0)
        worker_reference_multiplier = productivity_multiplier(
            float(round_context.get("worker_salary_actual", round_context.get("worker_avg_salary_actual", 0.0)) or 0.0),
            float(round_context.get("worker_avg_salary_actual", round_context.get("worker_salary_actual", 1.0)) or 1.0),
        )
        engineer_reference_multiplier = productivity_multiplier(
            float(round_context.get("engineer_salary_actual", round_context.get("engineer_avg_salary_actual", 0.0)) or 0.0),
            float(round_context.get("engineer_avg_salary_actual", round_context.get("engineer_salary_actual", 1.0)) or 1.0),
        )
        worker_base_productivity = worker_reference_productivity / max(worker_reference_multiplier, 1e-9)
        engineer_base_productivity = engineer_reference_productivity / max(engineer_reference_multiplier, 1e-9)

        worker_salary_ratio = salary_ratio(worker_salary, benchmark_worker_avg)
        engineer_salary_ratio = salary_ratio(engineer_salary, benchmark_engineer_avg)
        worker_productivity_multiplier = productivity_multiplier_from_ratio(worker_salary_ratio)
        engineer_productivity_multiplier = productivity_multiplier_from_ratio(engineer_salary_ratio)

        worker_adjusted_productivity = worker_base_productivity * worker_productivity_multiplier
        engineer_adjusted_productivity = engineer_base_productivity * engineer_productivity_multiplier
        worker_employees = float(worker_plan["working"])
        engineer_employees = float(engineer_plan["working"])
        worker_capacity = math.floor(max(worker_adjusted_productivity * worker_employees, 0.0))
        engineer_capacity = math.floor(max(engineer_adjusted_productivity * engineer_employees, 0.0))

        return {
            "workers": {
                "reference_productivity": worker_reference_productivity,
                "base_productivity": worker_base_productivity,
                "benchmark_salary": float(benchmark_worker_avg),
                "salary": float(worker_salary),
                "salary_ratio": float(worker_salary_ratio),
                "productivity_multiplier": float(worker_productivity_multiplier),
                "adjusted_productivity": float(worker_adjusted_productivity),
                "employees": worker_employees,
                "theoretical_capacity": float(worker_capacity),
            },
            "engineers": {
                "reference_productivity": engineer_reference_productivity,
                "base_productivity": engineer_base_productivity,
                "benchmark_salary": float(benchmark_engineer_avg),
                "salary": float(engineer_salary),
                "salary_ratio": float(engineer_salary_ratio),
                "productivity_multiplier": float(engineer_productivity_multiplier),
                "adjusted_productivity": float(engineer_adjusted_productivity),
                "employees": engineer_employees,
                "theoretical_capacity": float(engineer_capacity),
            },
        }

    def _validate(self, decision: SimulationInput, context: dict[str, Any]) -> list[str]:
        errors = []
        if decision.round_id not in self.round_contexts:
            errors.append(f"未知轮次：{decision.round_id}")
        if decision.workers < 0 or decision.engineers < 0 or decision.products_planned < 0:
            errors.append("工人数、工程师数和计划生产数量必须为非负数。")
        if decision.workers > MAX_HEADCOUNT or decision.engineers > MAX_HEADCOUNT:
            errors.append(f"工人数和工程师数不能超过 {MAX_HEADCOUNT:,}。")
        if decision.products_planned > MAX_PRODUCTS_PLANNED:
            errors.append(f"计划生产数量不能超过 {MAX_PRODUCTS_PLANNED:,}。")
        if not (1000 <= decision.worker_salary <= 10000):
            errors.append("工人工资必须在 ¥1,000 到 ¥10,000 之间。")
        if not (1000 <= decision.engineer_salary <= 10000):
            errors.append("工程师工资必须在 ¥1,000 到 ¥10,000 之间。")
        if decision.management_investment < 0 or decision.management_investment > MAX_INVESTMENT_AMOUNT:
            errors.append(f"管理投入必须在 ¥0 到 {_to_currency(MAX_INVESTMENT_AMOUNT)} 之间。")
        if decision.quality_investment < 0 or decision.quality_investment > MAX_INVESTMENT_AMOUNT:
            errors.append(f"质量投入必须在 ¥0 到 {_to_currency(MAX_INVESTMENT_AMOUNT)} 之间。")
        if decision.research_investment < 0 or decision.research_investment > MAX_INVESTMENT_AMOUNT:
            errors.append(f"研发投入必须在 ¥0 到 {_to_currency(MAX_INVESTMENT_AMOUNT)} 之间。")
        starting_cash = float(context.get("starting_cash", 0.0) or 0.0)
        starting_debt = float(context.get("starting_debt", 0.0) or 0.0)
        max_repayment = self._max_repayment_for_state(starting_cash, starting_debt)
        principal_after = starting_debt + decision.loan_delta
        if decision.loan_delta < -max_repayment - 1e-9:
            if starting_debt <= starting_cash + 1e-9:
                errors.append("还款金额不能超过当前负债。")
            else:
                errors.append("还款金额不能超过期初现金。")
        if decision.loan_delta > context["loan_limit"] + 1e-9:
            errors.append(f"新增贷款不能超过本轮贷款上限 {_to_currency(context['loan_limit'])}。")

        visible_markets = set(context["visible_markets"])
        chosen_markets = {
            market
            for market, market_decision in decision.market_decisions.items()
            if context["market_defaults"][market]["previous_agents"] + market_decision.agent_change > 0
        }
        if not chosen_markets:
            errors.append("本轮至少需要让一个市场的期末代理数大于 0。")
        if not set(decision.market_decisions).issubset(visible_markets):
            errors.append("市场决策必须限制在本轮开放的市场范围内。")

        for market, market_decision in decision.market_decisions.items():
            prev = context["market_defaults"][market]["previous_agents"]
            after = prev + market_decision.agent_change
            if after < 0:
                errors.append(f"{market}：渠道代理调整后数量不能为负数。")
            if abs(market_decision.agent_change) > MAX_AGENT_CHANGE_ABS:
                errors.append(f"{market}：渠道代理变化量不能超过 ±{MAX_AGENT_CHANGE_ABS:,}。")
            if after > 0 and not (3500 <= market_decision.price <= 25000):
                errors.append(f"{market}：售价必须在 ¥3,500 到 ¥25,000 之间。")
            if market_decision.marketing_investment < 0:
                errors.append(f"{market}：营销投入必须为非负数。")
            if market_decision.marketing_investment > MAX_INVESTMENT_AMOUNT:
                errors.append(f"{market}：营销投入不能超过 {_to_currency(MAX_INVESTMENT_AMOUNT)}。")
        return errors

    def _simulate_market_multiplayer(
        self,
        *,
        effective_decisions_by_team: dict[str, SimulationInput],
        contexts_by_team: dict[str, dict[str, Any]],
        current_home_city: str | None = None,
    ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
        round_id = next(iter(effective_decisions_by_team.values())).round_id
        visible_markets = list(self.key_data["markets"].keys())
        templates = self._market_templates_for_round(round_id, visible_markets)
        rows: list[dict[str, Any]] = []

        for team, decision in effective_decisions_by_team.items():
            context = contexts_by_team[team]
            total_people = decision.workers + decision.engineers
            management_index = decision.management_investment / total_people if total_people > 0 else 0.0
            previous_product_inventory = float(context.get("product_inventory_prev", 0.0) or 0.0)
            quality_denominator = previous_product_inventory * 1.2 + float(decision.products_planned)
            quality_index = decision.quality_investment / quality_denominator if quality_denominator > 0 else 0.0
            for market in visible_markets:
                template = templates.get(market)
                if template is None:
                    continue
                market_decision = decision.market_decisions[market]
                previous_agents = int(context["market_defaults"][market]["previous_agents"])
                after_agents = previous_agents + int(market_decision.agent_change)
                active_market = after_agents > 0
                price = float(market_decision.price) if active_market else 0.0
                marketing = float(market_decision.marketing_investment) if active_market else 0.0
                rows.append(
                    {
                        "round": round_id,
                        "market": market,
                        "team": team,
                        "management_index": management_index,
                        "agents": max(after_agents, 0),
                        "marketing_investment": marketing,
                        "quality_index": quality_index,
                        "price": price,
                        "sales_volume": 0.0,
                        "market_share": 0.0,
                        "market_size": template["market_size"],
                        "total_sales_volume": template["total_sales_volume"],
                        "avg_price": template["avg_price"],
                        "population": template["population"],
                        "penetration": template["penetration"],
                        "market_index": (1 + 0.1 * max(after_agents, 0)) * marketing if active_market else 0.0,
                        "source_file": template["source_file"],
                        "active_market": active_market,
                        "agents_before": previous_agents,
                        "agent_change": int(market_decision.agent_change),
                        "agents_after": max(after_agents, 0),
                    }
                )

        market_df = pd.DataFrame(rows)
        market_df["actual_real_cpi"] = np.nan
        market_df["competition"] = "EXSCHOOL"
        market_df = attach_lags(market_df)
        for team, context in contexts_by_team.items():
            team_mask = market_df["team"].astype(str) == str(team)
            market_df.loc[team_mask, "prev_team_management_index"] = float(context.get("campaign_previous_management_index", 0.0) or 0.0)
            market_df.loc[team_mask, "prev_team_quality_index"] = float(context.get("campaign_previous_quality_index", 0.0) or 0.0)
        market_df = clean_market_table(market_df)
        home_city_overrides = {
            str(team): str(context.get("current_home_city", "") or "").strip()
            for team, context in contexts_by_team.items()
            if str(context.get("current_home_city", "") or "").strip()
        }
        market_df = self._apply_home_city_to_frame(
            market_df,
            current_home_city,
            home_city_overrides=home_city_overrides,
        )

        feats = base_features(market_df)
        context_matrix = build_context(feats, self.round_levels, self.market_levels)
        X = build_tree_feature_matrix(feats, context_matrix).fillna(0.0)
        X = self._augment_model_matrix_with_home_city(X, market_df)

        _pred_log, _residual_log_shift, cpi_pred = self._predict_runtime_cpi_from_log_predictions(
            self.cpi_model["estimator"].predict(X.reindex(columns=self.cpi_model["columns"], fill_value=0.0)),
            market_df["market"],
            market_df["price"],
            rounds=market_df["round"],
            prev_marketshare_clean=market_df["prev_marketshare_clean"],
            prev_market_utilization_clean=market_df["prev_market_utilization_clean"],
        )
        share_X = self._build_cpi_to_share_feature_matrix(feats, cpi_pred)
        share_pred, _share_delta = predict_share_from_cpi_model(self.share_model, share_X, cpi_pred)

        scored = market_df.copy()
        scored["predicted_marketshare_unconstrained"] = share_pred
        scored["predicted_theoretical_cpi"] = cpi_pred
        scored["predicted_units_unconstrained"] = scored["predicted_marketshare_unconstrained"] * scored["market_size"]
        if "active_market" in scored.columns:
            inactive_mask = ~scored["active_market"].fillna(True)
            scored.loc[inactive_mask, "predicted_marketshare_unconstrained"] = 0.0
            scored.loc[inactive_mask, "predicted_theoretical_cpi"] = 0.0
            scored.loc[inactive_mask, "predicted_units_unconstrained"] = 0.0

        team_total_products = {
            team: float(contexts_by_team[team].get("product_inventory_prev", 0.0) or 0.0) + float(decision.products_planned)
            for team, decision in effective_decisions_by_team.items()
        }
        scored = allocate_sales_with_gap_absorption(scored, team_total_products)

        team_frames: dict[str, pd.DataFrame] = {}
        for team, team_rows in scored.groupby("team"):
            team_rows = team_rows.copy()
            team_rows["simulated_sales_volume"] = team_rows["final_sales"]
            team_rows["simulated_marketshare"] = team_rows["simulated_sales_volume"] / team_rows["market_size"]
            team_rows["simulated_sales_revenue"] = team_rows["simulated_sales_volume"] * team_rows["price"]
            team_frames[str(team)] = team_rows

        return scored, team_frames

    def _report_stub_for_state_transition(
        self,
        *,
        context: dict[str, Any],
        decision: SimulationInput,
        outcome: dict[str, Any],
        team_market_df: pd.DataFrame,
    ) -> dict[str, Any]:
        total_people = decision.workers + decision.engineers
        quality_denominator = float(context.get("product_inventory_prev", 0.0) or 0.0) * 1.2 + float(outcome["new_products"])
        return {
            "ending_cash": float(outcome["ending_cash"]),
            "ending_debt": float(outcome["ending_debt"]),
            "worker_plan": outcome["worker_plan"],
            "engineer_plan": outcome["engineer_plan"],
            "market_results": [
                {"market": row["market"], "agents_after": int(row["agents_after"])}
                for _, row in team_market_df.sort_values("market").iterrows()
            ],
            "management_index": float(decision.management_investment) / total_people if total_people > 0 else 0.0,
            "quality_index": float(decision.quality_investment) / quality_denominator if quality_denominator > 0 else 0.0,
            "storage_summary": [
                {
                    "item": "Components",
                    "capacity_after": max(float(context.get("component_capacity_prev", 0.0) or 0.0), float(outcome["total_components_available"])),
                },
                {
                    "item": "Products",
                    "capacity_after": max(float(context.get("product_capacity_prev", 0.0) or 0.0), float(outcome["total_products_available"])),
                },
            ],
            "component_inventory_end": float(outcome["leftover_components"]),
            "product_inventory_end": float(outcome["leftover_products"]),
            "active_patents_next_round": int(outcome["active_patents_after"]),
            "accumulated_research_investment_next_round": float(outcome["accumulated_research_next"]),
        }

    def _states_before_round(
        self,
        round_id: str,
        *,
        current_home_city: str | None = None,
        game_id: str | None = None,
    ) -> dict[str, CampaignState | None]:
        target_order = round_sort_key(round_id)
        states: dict[str, CampaignState | None] = {team: None for team in self.team_ids}
        for prior_round_id in self.available_rounds():
            if round_sort_key(prior_round_id) >= target_order:
                break
            decisions_by_team = self._round_decisions(prior_round_id)
            contexts_by_team = {
                team: self._context_for_company_state(
                    prior_round_id,
                    team,
                    states.get(team),
                    current_home_city=current_home_city if team == TEAM_ID else None,
                    game_id=game_id,
                )
                for team in decisions_by_team
            }
            prev_worker_avg, prev_engineer_avg = self._previous_global_average_salary(states)
            benchmark_worker_avg, benchmark_engineer_avg = self._current_global_average_salary_for_multiplayer(
                decisions_by_team,
                prev_worker_avg,
                prev_engineer_avg,
            )
            effective_by_team = {
                team: self._effective_decision_for_team(
                    team,
                    decisions_by_team[team],
                    contexts_by_team[team],
                    starting_cash=float(contexts_by_team[team]["starting_cash"]),
                    starting_debt=float(contexts_by_team[team]["starting_debt"]),
                    benchmark_worker_avg=benchmark_worker_avg,
                    benchmark_engineer_avg=benchmark_engineer_avg,
                )
                for team in decisions_by_team
            }
            _, team_frames = self._simulate_market_multiplayer(
                effective_decisions_by_team=effective_by_team,
                contexts_by_team=contexts_by_team,
                current_home_city=current_home_city,
            )
            next_states: dict[str, CampaignState | None] = {}
            for team in self.team_ids:
                decision = decisions_by_team.get(team)
                team_context = contexts_by_team.get(team)
                team_market_df = team_frames.get(team)
                if decision is None or team_context is None or team_market_df is None:
                    next_states[team] = states.get(team)
                    continue
                outcome = self._financial_outcome_for_team(
                    team,
                    team_market_df,
                    effective_by_team[team],
                    team_context,
                    float(team_context["starting_cash"]),
                    float(team_context["starting_debt"]),
                    benchmark_worker_avg=benchmark_worker_avg,
                    benchmark_engineer_avg=benchmark_engineer_avg,
                )
                report_stub = self._report_stub_for_state_transition(
                    context=team_context,
                    decision=effective_by_team[team],
                    outcome=outcome,
                    team_market_df=team_market_df,
                )
                next_states[team] = next_campaign_state(
                    report=report_stub,
                    decision=decision,
                    state=states.get(team),
                )
                next_states[team] = CampaignState(
                    **{
                        **next_states[team].__dict__,
                        "worker_avg_salary": float(benchmark_worker_avg),
                        "engineer_avg_salary": float(benchmark_engineer_avg),
                    }
                )
            states = next_states
        return states

    def _simulate_market(self, decision: SimulationInput, context: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame]:
        current_home_city = str(context.get("current_home_city", "") or "")
        game_id = str(context.get("game_id", "default"))
        states = self._states_before_round(
            decision.round_id,
            current_home_city=current_home_city or None,
            game_id=game_id,
        )
        decisions_by_team = self._round_decisions(decision.round_id, team13_decision=decision)
        contexts_by_team = {
            team: self._context_for_company_state(
                decision.round_id,
                team,
                states.get(team),
                current_home_city=current_home_city if team == TEAM_ID else None,
                game_id=game_id,
            )
            for team in decisions_by_team
        }
        prev_worker_avg, prev_engineer_avg = self._previous_global_average_salary(states)
        benchmark_worker_avg, benchmark_engineer_avg = self._current_global_average_salary_for_multiplayer(
            decisions_by_team,
            prev_worker_avg,
            prev_engineer_avg,
        )
        effective_by_team = {
            team: self._effective_decision_for_team(
                team,
                decisions_by_team[team],
                contexts_by_team[team],
                starting_cash=float(contexts_by_team[team]["starting_cash"]),
                starting_debt=float(contexts_by_team[team]["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )
            for team in decisions_by_team
        }
        full_market_df, team_frames = self._simulate_market_multiplayer(
            effective_decisions_by_team=effective_by_team,
            contexts_by_team=contexts_by_team,
            current_home_city=current_home_city or None,
        )
        return full_market_df, team_frames[TEAM_ID].copy()

    def _market_report_cost_from_decision(self, decision: SimulationInput) -> float:
        subscriptions = sum(1 for item in decision.market_decisions.values() if item.subscribed_market_report)
        return market_report_cost_from_decision(subscriptions, MARKET_REPORT_SUBSCRIPTION_COST)

    def _effective_decision_for_team(
        self,
        team: str,
        decision: SimulationInput,
        context: dict[str, Any],
        *,
        starting_cash: float,
        starting_debt: float,
        benchmark_worker_avg: float | None = None,
        benchmark_engineer_avg: float | None = None,
    ) -> SimulationInput:
        if self.single_player_mode == "real-original" and str(team) != TEAM_ID:
            return decision
        return self._apply_cash_break_to_decision(
            decision,
            context,
            starting_cash=starting_cash,
            starting_debt=starting_debt,
            benchmark_worker_avg=benchmark_worker_avg,
            benchmark_engineer_avg=benchmark_engineer_avg,
        )

    def _effective_decision_for_multiplayer_team(
        self,
        team: str,
        decision: SimulationInput,
        context: dict[str, Any],
        *,
        mutable_teams: set[str],
        starting_cash: float,
        starting_debt: float,
        benchmark_worker_avg: float,
        benchmark_engineer_avg: float,
    ) -> SimulationInput:
        if str(team) not in mutable_teams and self.single_player_mode == "real-original":
            return decision
        return self._apply_cash_break_to_decision(
            decision,
            context,
            starting_cash=starting_cash,
            starting_debt=starting_debt,
            benchmark_worker_avg=benchmark_worker_avg,
            benchmark_engineer_avg=benchmark_engineer_avg,
        )

    def _apply_cash_break_to_decision(
        self,
        decision: SimulationInput,
        context: dict[str, Any],
        *,
        starting_cash: float,
        starting_debt: float,
        benchmark_worker_avg: float | None = None,
        benchmark_engineer_avg: float | None = None,
    ) -> SimulationInput:
        normalized_loan_delta = self._clamp_loan_delta(
            float(decision.loan_delta),
            {
                **context,
                "starting_cash": float(starting_cash),
                "starting_debt": float(starting_debt),
            },
        )
        running_cash = max(float(starting_cash) + normalized_loan_delta, 0.0)
        current_worker_avg = float(benchmark_worker_avg) if benchmark_worker_avg is not None else None
        current_engineer_avg = float(benchmark_engineer_avg) if benchmark_engineer_avg is not None else None
        if current_worker_avg is None or current_engineer_avg is None:
            current_worker_avg, current_engineer_avg = self._current_global_average_salary(
                decision.round_id,
                decision,
                float(context.get("worker_avg_salary_prev", decision.worker_salary)),
                float(context.get("engineer_avg_salary_prev", decision.engineer_salary)),
            )

        worker_plan = workforce_plan(
            requested_total=decision.workers,
            requested_salary=decision.worker_salary,
            benchmark_average_salary=current_worker_avg,
            previous_recent=int(context.get("worker_recent_prev", context.get("workers_actual", 0))),
            previous_mature=int(context.get("worker_mature_prev", 0)),
            previous_experienced=int(context.get("worker_experienced_prev", 0)),
        )
        engineer_plan = workforce_plan(
            requested_total=decision.engineers,
            requested_salary=decision.engineer_salary,
            benchmark_average_salary=current_engineer_avg,
            previous_recent=int(context.get("engineer_recent_prev", context.get("engineers_actual", 0))),
            previous_mature=int(context.get("engineer_mature_prev", 0)),
            previous_experienced=int(context.get("engineer_experienced_prev", 0)),
        )
        actual_workers = int(worker_plan["working"])
        actual_engineers = int(engineer_plan["working"])
        capacity_details = self._capacity_details(
            round_id=decision.round_id,
            worker_plan=worker_plan,
            engineer_plan=engineer_plan,
            worker_salary=decision.worker_salary,
            engineer_salary=decision.engineer_salary,
            benchmark_worker_avg=current_worker_avg,
            benchmark_engineer_avg=current_engineer_avg,
        )

        # Fixed payroll is still deducted first; later production/investment lanes
        # are clipped to whatever cash remains.
        payroll_costs = [
            actual_workers * decision.worker_salary * 3.0,
            actual_engineers * decision.engineer_salary * 3.0,
            worker_plan["laid_off"] * float(context.get("worker_avg_salary_prev", decision.worker_salary)),
            engineer_plan["laid_off"] * float(context.get("engineer_avg_salary_prev", decision.engineer_salary)),
            worker_plan["quits"] * float(context.get("worker_avg_salary_prev", decision.worker_salary)) * 2.0,
            engineer_plan["quits"] * float(context.get("engineer_avg_salary_prev", decision.engineer_salary)) * 2.0,
        ]
        for amount in payroll_costs:
            paid = min(float(amount), running_cash)
            running_cash -= paid
            if running_cash <= 0:
                running_cash = 0.0
                break

        component_storage_unit_cost = float(context.get("component_storage_unit_cost", self.component_storage_unit_cost))
        product_storage_unit_cost = float(context.get("product_storage_unit_cost", self.product_storage_unit_cost))
        patent_multiplier = patent_cost_multiplier(int(context.get("active_patents_prev", 0) or 0))
        component_material_unit = float(context["component_material_price"]) * patent_multiplier
        product_material_unit = float(context["product_material_price"]) * patent_multiplier
        previous_component_capacity = float(context.get("component_capacity_prev", 0.0) or 0.0)
        previous_product_capacity = float(context.get("product_capacity_prev", 0.0) or 0.0)
        previous_component_inventory = float(context.get("component_inventory_prev", 0.0) or 0.0)
        previous_product_inventory = float(context.get("product_inventory_prev", 0.0) or 0.0)
        worker_capacity = float(capacity_details["workers"]["theoretical_capacity"])
        engineer_capacity = float(capacity_details["engineers"]["theoretical_capacity"])
        production_snapshot = resolve_affordable_production(
            requested_products=int(decision.products_planned),
            available_cash=running_cash,
            previous_component_inventory=previous_component_inventory,
            previous_product_inventory=previous_product_inventory,
            previous_component_capacity=previous_component_capacity,
            previous_product_capacity=previous_product_capacity,
            component_material_price=component_material_unit,
            product_material_price=product_material_unit,
            component_storage_unit_cost=component_storage_unit_cost,
            product_storage_unit_cost=product_storage_unit_cost,
            patent_multiplier=1.0,
            worker_capacity=worker_capacity,
            engineer_capacity=engineer_capacity,
        )

        production_paid = min(production_snapshot.total_cost, running_cash)
        running_cash -= production_paid
        if running_cash <= 0:
            running_cash = 0.0

        adjusted_markets: dict[str, MarketDecision] = {}
        for market, item in decision.market_decisions.items():
            adjusted_markets[market] = MarketDecision(
                agent_change=0,
                marketing_investment=0.0,
                price=item.price,
                subscribed_market_report=False,
            )

        for market, item in decision.market_decisions.items():
            requested_change = int(item.agent_change)
            if requested_change == 0:
                adjusted_markets[market] = MarketDecision(
                    agent_change=0,
                    marketing_investment=0.0,
                    price=item.price,
                    subscribed_market_report=False,
                )
                continue
            unit_cost = 300_000.0 if requested_change > 0 else 100_000.0
            affordable_units = int(running_cash // unit_cost) if unit_cost > 0 else abs(requested_change)
            realized_units = min(abs(requested_change), max(affordable_units, 0))
            realized_change = realized_units if requested_change > 0 else -realized_units
            running_cash -= realized_units * unit_cost
            if running_cash <= 0:
                running_cash = 0.0
            adjusted_markets[market] = MarketDecision(
                agent_change=realized_change,
                marketing_investment=0.0,
                price=item.price,
                subscribed_market_report=False,
            )

        for market, item in decision.market_decisions.items():
            adjusted_markets[market] = MarketDecision(
                agent_change=adjusted_markets[market].agent_change,
                marketing_investment=0.0,
                price=adjusted_markets[market].price,
                subscribed_market_report=item.subscribed_market_report,
            )

        for market, item in decision.market_decisions.items():
            previous_agents = int(context["market_defaults"].get(market, {}).get("previous_agents", 0) or 0)
            after_agents = previous_agents + int(adjusted_markets[market].agent_change)
            if after_agents <= 0:
                adjusted_markets[market] = MarketDecision(
                    agent_change=adjusted_markets[market].agent_change,
                    marketing_investment=0.0,
                    price=adjusted_markets[market].price,
                    subscribed_market_report=adjusted_markets[market].subscribed_market_report,
                )
                continue
            realized_marketing = min(float(item.marketing_investment), running_cash)
            running_cash -= realized_marketing
            adjusted_markets[market] = MarketDecision(
                agent_change=adjusted_markets[market].agent_change,
                marketing_investment=realized_marketing,
                price=adjusted_markets[market].price,
                subscribed_market_report=adjusted_markets[market].subscribed_market_report,
            )
            if running_cash <= 0:
                running_cash = 0.0

        quality_denominator = production_snapshot.quality_denominator
        realized_quality = min(float(decision.quality_investment), running_cash) if quality_denominator > 0 else 0.0
        running_cash -= realized_quality
        realized_management = min(float(decision.management_investment), running_cash)
        running_cash -= realized_management

        return SimulationInput(
            round_id=decision.round_id,
            loan_delta=normalized_loan_delta,
            workers=actual_workers,
            engineers=actual_engineers,
            worker_salary=decision.worker_salary,
            engineer_salary=decision.engineer_salary,
            management_investment=realized_management,
            quality_investment=realized_quality,
            research_investment=decision.research_investment,
            products_planned=production_snapshot.new_products,
            market_decisions=adjusted_markets,
        )

    def _fixed_decision_for_team(self, round_id: str, team: str) -> SimulationInput | None:
        if self.fixed_decisions_df.empty:
            return None
        rows = self.fixed_decisions_df[
            (self.fixed_decisions_df["round_id"] == round_id) & (self.fixed_decisions_df["team"] == team)
        ]
        if rows.empty:
            return None
        row = rows.iloc[0]
        round_market_defaults = self.round_contexts.get(round_id, {}).get("market_defaults", {})
        default_selected_markets = {
            market for market, values in round_market_defaults.items() if self._default_market_report_subscription(values)
        }
        explicit_selected_markets: set[str] = set()
        has_explicit_selection = False

        def parse_selected_market_report(prefix: str) -> bool | None:
            raw_selected = row.get(f"{prefix}_selected")
            if pd.isna(raw_selected):
                raw_selected = None
            if isinstance(raw_selected, str):
                raw_selected = raw_selected.strip().lower()
                if not raw_selected:
                    raw_selected = None
                elif raw_selected in {"false", "no"}:
                    return False
            if raw_selected is None:
                return None
            try:
                return bool(int(float(raw_selected)))
            except (TypeError, ValueError):
                return bool(raw_selected)

        for market in self.key_data["markets"].keys():
            parsed = parse_selected_market_report(market.lower())
            if parsed is None:
                continue
            has_explicit_selection = True
            if parsed:
                explicit_selected_markets.add(market)

        use_explicit_selection = has_explicit_selection

        market_decisions: dict[str, MarketDecision] = {}
        for market in self.key_data["markets"].keys():
            prefix = market.lower()
            market_decisions[market] = MarketDecision(
                agent_change=int(row.get(f"{prefix}_agent_change", 0) or 0),
                marketing_investment=float(row.get(f"{prefix}_marketing_investment", 0.0) or 0.0),
                price=float(row.get(f"{prefix}_price", 0.0) or 0.0),
                subscribed_market_report=(
                    market in explicit_selected_markets if use_explicit_selection else market in default_selected_markets
                ),
            )
        return SimulationInput(
            round_id=round_id,
            loan_delta=float(row.get("loan_delta", 0.0) or 0.0),
            workers=int(row.get("workers", 0) or 0),
            engineers=int(row.get("engineers", 0) or 0),
            worker_salary=float(row.get("worker_salary", 0.0) or 0.0),
            engineer_salary=float(row.get("engineer_salary", 0.0) or 0.0),
            management_investment=float(row.get("management_investment", 0.0) or 0.0),
            quality_investment=float(row.get("quality_investment", 0.0) or 0.0),
            research_investment=float(row.get("research_investment", 0.0) or 0.0),
            products_planned=int(row.get("products_planned", 0) or 0),
            market_decisions=market_decisions,
        )

    def _financial_outcome_for_team(
        self,
        team: str,
        team_market_df: pd.DataFrame,
        decision: SimulationInput,
        context: dict[str, Any],
        starting_cash: float,
        starting_debt: float,
        benchmark_worker_avg: float | None = None,
        benchmark_engineer_avg: float | None = None,
    ) -> dict[str, Any]:
        previous_component_inventory = float(context.get("component_inventory_prev", 0.0) or 0.0)
        previous_product_inventory = float(context.get("product_inventory_prev", 0.0) or 0.0)
        active_patents = int(context.get("active_patents_prev", 0) or 0)
        revenue = float(team_market_df["simulated_sales_revenue"].sum()) if not team_market_df.empty else 0.0
        sold_units = float(team_market_df["simulated_sales_volume"].sum()) if not team_market_df.empty else 0.0
        target_products = float(decision.products_planned)
        current_worker_avg = float(benchmark_worker_avg) if benchmark_worker_avg is not None else None
        current_engineer_avg = float(benchmark_engineer_avg) if benchmark_engineer_avg is not None else None
        if current_worker_avg is None or current_engineer_avg is None:
            current_worker_avg, current_engineer_avg = self._current_global_average_salary(
                decision.round_id,
                decision,
                float(context.get("worker_avg_salary_prev", decision.worker_salary)),
                float(context.get("engineer_avg_salary_prev", decision.engineer_salary)),
            )
        worker_plan = workforce_plan(
            requested_total=decision.workers,
            requested_salary=decision.worker_salary,
            benchmark_average_salary=current_worker_avg,
            previous_recent=int(context.get("worker_recent_prev", context.get("workers_actual", 0))),
            previous_mature=int(context.get("worker_mature_prev", 0)),
            previous_experienced=int(context.get("worker_experienced_prev", 0)),
        )
        engineer_plan = workforce_plan(
            requested_total=decision.engineers,
            requested_salary=decision.engineer_salary,
            benchmark_average_salary=current_engineer_avg,
            previous_recent=int(context.get("engineer_recent_prev", context.get("engineers_actual", 0))),
            previous_mature=int(context.get("engineer_mature_prev", 0)),
            previous_experienced=int(context.get("engineer_experienced_prev", 0)),
        )
        capacity_details = self._capacity_details(
            round_id=decision.round_id,
            worker_plan=worker_plan,
            engineer_plan=engineer_plan,
            worker_salary=decision.worker_salary,
            engineer_salary=decision.engineer_salary,
            benchmark_worker_avg=current_worker_avg,
            benchmark_engineer_avg=current_engineer_avg,
        )

        principal_after = max(float(starting_debt) + float(decision.loan_delta), 0.0)
        interest = principal_after * float(context["interest_rate"])

        agent_change_cost = 0.0
        for market_decision in decision.market_decisions.values():
            if market_decision.agent_change >= 0:
                agent_change_cost += market_decision.agent_change * 300_000.0
            else:
                agent_change_cost += abs(market_decision.agent_change) * 100_000.0

        patent_multiplier = patent_cost_multiplier(active_patents)
        worker_capacity = float(capacity_details["workers"]["theoretical_capacity"])
        engineer_capacity = float(capacity_details["engineers"]["theoretical_capacity"])
        target_component_need = max(float(target_products) * 7.0 - previous_component_inventory, 0.0)
        component_units = min(target_component_need, worker_capacity)
        total_components_available = previous_component_inventory + component_units
        new_products = int(min(float(decision.products_planned), engineer_capacity, math.floor(total_components_available / 7.0)))
        production_snapshot = build_production_snapshot(
            target_products=int(target_products),
            new_products=new_products,
            component_units=component_units,
            previous_component_inventory=previous_component_inventory,
            previous_product_inventory=previous_product_inventory,
            previous_component_capacity=float(context.get("component_capacity_prev", 0.0) or 0.0),
            previous_product_capacity=float(context.get("product_capacity_prev", 0.0) or 0.0),
            component_material_price=float(context["component_material_price"]),
            product_material_price=float(context["product_material_price"]),
            component_storage_unit_cost=float(context.get("component_storage_unit_cost", self.component_storage_unit_cost)),
            product_storage_unit_cost=float(context.get("product_storage_unit_cost", self.product_storage_unit_cost)),
            patent_multiplier=patent_multiplier,
        )
        component_units = production_snapshot.component_units
        components_total = production_snapshot.components_total
        components_used = production_snapshot.components_used
        leftover_components = production_snapshot.leftover_components
        total_products_available = production_snapshot.total_products_available
        leftover_products = max(total_products_available - sold_units, 0.0)
        component_material_cost = production_snapshot.component_material_cost
        product_material_cost = production_snapshot.product_material_cost
        component_storage_increment = production_snapshot.component_storage_increment
        product_storage_increment = production_snapshot.product_storage_increment
        component_storage_cost = production_snapshot.component_storage_cost
        product_storage_cost = production_snapshot.product_storage_cost
        workers_salary_cost = float(decision.workers) * float(decision.worker_salary) * 3.0
        engineers_salary_cost = float(decision.engineers) * float(decision.engineer_salary) * 3.0
        layoff_cost = (
            worker_plan["laid_off"] * float(context.get("worker_avg_salary_prev", decision.worker_salary))
            + engineer_plan["laid_off"] * float(context.get("engineer_avg_salary_prev", decision.engineer_salary))
        )
        salary_reduction_penalty = (
            worker_plan["quits"] * float(context.get("worker_avg_salary_prev", decision.worker_salary)) * 2.0
            + engineer_plan["quits"] * float(context.get("engineer_avg_salary_prev", decision.engineer_salary)) * 2.0
        )
        marketing_investment = float(sum(item.marketing_investment for item in decision.market_decisions.values()))
        planned_subscribed_markets = [
            market for market, item in decision.market_decisions.items() if item.subscribed_market_report
        ]
        cash_before_market_report = (
            float(starting_cash)
            + float(decision.loan_delta)
            - workers_salary_cost
            - engineers_salary_cost
            - component_material_cost
            - component_storage_cost
            - product_material_cost
            - product_storage_cost
            - layoff_cost
            - salary_reduction_penalty
            - agent_change_cost
            - marketing_investment
            - float(decision.quality_investment)
            - float(decision.management_investment)
            + revenue
        )
        affordable_subscriptions = 0
        if MARKET_REPORT_SUBSCRIPTION_COST > 0:
            affordable_subscriptions = max(
                min(
                    int(max(cash_before_market_report, 0.0) // MARKET_REPORT_SUBSCRIPTION_COST),
                    len(planned_subscribed_markets),
                ),
                0,
            )
        realized_subscribed_markets = planned_subscribed_markets[:affordable_subscriptions]
        market_report_cost = float(affordable_subscriptions) * MARKET_REPORT_SUBSCRIPTION_COST
        cash_before_research = max(cash_before_market_report - market_report_cost, 0.0)
        research_investment = min(float(decision.research_investment), cash_before_research)
        accumulated_research_prev = float(context.get("accumulated_research_investment_prev", 0.0) or 0.0)
        research_pool = accumulated_research_prev + research_investment
        research_probability = research_success_probability(research_pool)
        research_roll = deterministic_uniform(
            context.get("game_id", "default"),
            decision.round_id,
            team,
            starting_cash,
            starting_debt,
            accumulated_research_prev,
            research_investment,
        )
        patents_added = 1 if research_pool > 0 and research_roll < research_probability else 0
        accumulated_research_next = 0.0 if patents_added > 0 else research_pool
        patents_after = active_patents + patents_added

        pretax_profit = (
            revenue
            - workers_salary_cost
            - engineers_salary_cost
            - component_material_cost
            - component_storage_cost
            - product_material_cost
            - product_storage_cost
            - layoff_cost
            - salary_reduction_penalty
            - agent_change_cost
            - marketing_investment
            - float(decision.quality_investment)
            - float(decision.management_investment)
            - market_report_cost
            - research_investment
            - interest
        )
        tax = max(pretax_profit, 0.0) * DEFAULT_TAX_RATE
        ending_cash = (
            float(starting_cash)
            + float(decision.loan_delta)
            - workers_salary_cost
            - engineers_salary_cost
            - component_material_cost
            - component_storage_cost
            - product_material_cost
            - product_storage_cost
            - layoff_cost
            - salary_reduction_penalty
            - agent_change_cost
            - marketing_investment
            - float(decision.quality_investment)
            - float(decision.management_investment)
            + revenue
            - market_report_cost
            - research_investment
            - tax
        )
        ending_cash = max(ending_cash, 0.0)
        total_assets = ending_cash
        ending_debt = principal_after + interest
        net_assets = total_assets - ending_debt
        net_profit = pretax_profit - tax

        return {
            "revenue": revenue,
            "sold_units": sold_units,
            "leftover_products": leftover_products,
            "leftover_components": leftover_components,
            "previous_product_inventory": previous_product_inventory,
            "previous_component_inventory": previous_component_inventory,
            "new_products": new_products,
            "new_components": component_units,
            "total_products_available": total_products_available,
            "total_components_available": components_total,
            "components_used": components_used,
            "principal_after": principal_after,
            "interest": interest,
            "agent_change_cost": agent_change_cost,
            "component_units": component_units,
            "component_material_cost": component_material_cost,
            "product_material_cost": product_material_cost,
            "component_storage_cost": component_storage_cost,
            "product_storage_cost": product_storage_cost,
            "workers_salary_cost": workers_salary_cost,
            "engineers_salary_cost": engineers_salary_cost,
            "layoff_cost": layoff_cost,
            "salary_reduction_penalty": salary_reduction_penalty,
            "marketing_investment": marketing_investment,
            "market_report_cost": market_report_cost,
            "realized_subscribed_markets": realized_subscribed_markets,
            "research_investment": research_investment,
            "research_probability": research_probability,
            "research_roll": research_roll,
            "patents_added": patents_added,
            "active_patents_before": active_patents,
            "active_patents_after": patents_after,
            "accumulated_research_prev": accumulated_research_prev,
            "accumulated_research_next": accumulated_research_next,
            "pretax_profit": pretax_profit,
            "tax": tax,
            "ending_cash": ending_cash,
            "leftover_inventory_value": 0.0,
            "total_assets": total_assets,
            "ending_debt": ending_debt,
            "net_assets": net_assets,
            "net_profit": net_profit,
            "worker_plan": worker_plan,
            "engineer_plan": engineer_plan,
            "worker_capacity_detail": capacity_details["workers"],
            "engineer_capacity_detail": capacity_details["engineers"],
            "component_storage_increment": component_storage_increment,
            "product_storage_increment": product_storage_increment,
        }

    def _loan_limit_for_state(self, starting_cash: float, starting_debt: float, home_city: str | None) -> float:
        city = str(home_city or "Shanghai")
        city_data = self.key_data["markets"].get(city) or next(iter(self.key_data["markets"].values()))
        initial_limit = float(city_data.get("initial_max_loan", 5_000_000.0))
        return loan_limit_for_state(
            initial_limit=initial_limit,
            starting_cash=starting_cash,
            starting_debt=starting_debt,
            stage_thresholds=LOAN_STAGE_THRESHOLDS,
        )

    def _compute_all_company_results(
        self,
        *,
        full_market_df: pd.DataFrame,
        our_decision: SimulationInput,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        current_home_city = str(context.get("current_home_city", "") or "")
        game_id = str(context.get("game_id", "default"))
        team_states = self._states_before_round(
            our_decision.round_id,
            current_home_city=current_home_city or None,
            game_id=game_id,
        )
        team_states[TEAM_ID] = self._campaign_state_from_context(context)
        decisions_by_team = self._round_decisions(our_decision.round_id, team13_decision=our_decision)
        contexts_by_team = {
            team: self._context_for_company_state(
                our_decision.round_id,
                team,
                team_states.get(team),
                current_home_city=current_home_city if team == TEAM_ID else None,
                game_id=game_id,
            )
            for team in decisions_by_team
        }
        prev_worker_avg, prev_engineer_avg = self._previous_global_average_salary(team_states)
        benchmark_worker_avg, benchmark_engineer_avg = self._current_global_average_salary_for_multiplayer(
            decisions_by_team,
            prev_worker_avg,
            prev_engineer_avg,
        )
        effective_by_team = {
            team: self._effective_decision_for_team(
                team,
                decisions_by_team[team],
                contexts_by_team[team],
                starting_cash=float(contexts_by_team[team]["starting_cash"]),
                starting_debt=float(contexts_by_team[team]["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )
            for team in decisions_by_team
        }
        standings: list[dict[str, Any]] = []
        for team, team_rows in full_market_df.groupby("team"):
            team = str(team)
            decision = effective_by_team.get(team)
            team_context = contexts_by_team.get(team)
            if decision is None or team_context is None:
                continue

            company_rows = team_rows.copy()
            company_rows["simulated_sales_volume"] = company_rows["final_sales"]
            company_rows["simulated_sales_revenue"] = company_rows["simulated_sales_volume"] * company_rows["price"]
            outcome = self._financial_outcome_for_team(
                team,
                company_rows,
                decision,
                team_context,
                float(team_context["starting_cash"]),
                float(team_context["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )
            standings.append(
                {
                    "team": team,
                    "net_assets": float(outcome["net_assets"]),
                    "ending_cash": float(outcome["ending_cash"]),
                    "ending_debt": float(outcome["ending_debt"]),
                    "net_profit": float(outcome["net_profit"]),
                    "sales_revenue": float(outcome["revenue"]),
                }
            )

        standings = sorted(standings, key=lambda row: row["net_assets"], reverse=True)
        for idx, row in enumerate(standings, start=1):
            row["rank"] = idx
        return {
            "standings": standings,
            "net_assets_point": {
                "round_id": our_decision.round_id,
                "standings": [{"team": row["team"], "net_assets": row["net_assets"], "rank": row["rank"]} for row in standings],
            },
        }

    def _assemble_company_results_from_outcomes(self, round_id: str, outcomes_by_team: dict[str, dict[str, Any]]) -> dict[str, Any]:
        standings = [
            {
                "team": team,
                "net_assets": float(outcome["net_assets"]),
                "ending_cash": float(outcome["ending_cash"]),
                "ending_debt": float(outcome["ending_debt"]),
                "net_profit": float(outcome["net_profit"]),
                "sales_revenue": float(outcome["revenue"]),
            }
            for team, outcome in outcomes_by_team.items()
        ]
        standings = sorted(standings, key=lambda row: row["net_assets"], reverse=True)
        for idx, row in enumerate(standings, start=1):
            row["rank"] = idx
        return {
            "standings": standings,
            "net_assets_point": {
                "round_id": round_id,
                "standings": [{"team": row["team"], "net_assets": row["net_assets"], "rank": row["rank"]} for row in standings],
            },
        }

    def _decision_matches_fixed_decision(
        self,
        round_id: str,
        team: str,
        decision: SimulationInput,
        context: dict[str, Any] | None = None,
    ) -> bool:
        fixed = self._fixed_decision_for_team(round_id, team)
        if fixed is None:
            return False

        def close(left: float, right: float, tolerance: float = 1e-6) -> bool:
            return abs(float(left) - float(right)) <= tolerance

        previous_workers = int((context or {}).get("workers_actual", 0) or 0)
        previous_engineers = int((context or {}).get("engineers_actual", 0) or 0)
        workers_match = int(decision.workers) == int(fixed.workers) or int(decision.workers) == int(fixed.workers) + previous_workers
        engineers_match = int(decision.engineers) == int(fixed.engineers) or int(decision.engineers) == int(fixed.engineers) + previous_engineers
        scalar_matches = (
            close(decision.loan_delta, fixed.loan_delta)
            and workers_match
            and engineers_match
            and close(decision.management_investment, fixed.management_investment)
            and close(decision.quality_investment, fixed.quality_investment)
            and int(decision.products_planned) == int(fixed.products_planned)
        )
        if not scalar_matches:
            return False
        for market in self.key_data["markets"].keys():
            requested = decision.market_decisions.get(market)
            expected = fixed.market_decisions.get(market)
            if requested is None or expected is None:
                return False
            if int(requested.agent_change) != int(expected.agent_change):
                return False
            if not close(requested.marketing_investment, expected.marketing_investment):
                return False
            active = int(requested.agent_change) != 0 or float(requested.marketing_investment) > 0 or float(expected.marketing_investment) > 0
            if active and not close(requested.price, expected.price):
                return False
        return True

    def _can_replay_real_original_report(self, decision: SimulationInput, context: dict[str, Any]) -> bool:
        if self.single_player_mode != "real-original":
            return False
        current_home_city = str(context.get("current_home_city", "") or "").strip()
        original_home_city = self.team_home_city_map.get(TEAM_ID, "Shanghai")
        if current_home_city and current_home_city != original_home_city:
            return False
        return self._decision_matches_fixed_decision(decision.round_id, TEAM_ID, decision, context)

    def _fixed_summary_company_results(self, round_id: str) -> dict[str, Any]:
        rows = self.fixed_round_summary_df[self.fixed_round_summary_df["round_id"] == round_id].copy()
        standings: list[dict[str, Any]] = []
        for _, row in rows.iterrows():
            ending_cash = float(row.get("ending_cash_est", 0.0) or 0.0)
            ending_debt = float(row.get("ending_debt_est", 0.0) or 0.0)
            starting_cash = float(row.get("starting_cash_est", 0.0) or 0.0)
            loan_delta = float(row.get("loan_delta_est", 0.0) or 0.0)
            sales_revenue = float(row.get("sales_revenue_source", 0.0) or 0.0)
            net_assets = ending_cash - ending_debt
            standings.append(
                {
                    "team": str(row["team"]),
                    "net_assets": net_assets,
                    "ending_cash": ending_cash,
                    "ending_debt": ending_debt,
                    "net_profit": ending_cash - starting_cash - loan_delta,
                    "sales_revenue": sales_revenue,
                }
            )
        standings = sorted(standings, key=lambda item: item["net_assets"], reverse=True)
        for idx, row in enumerate(standings, start=1):
            row["rank"] = idx
        return {
            "standings": standings,
            "net_assets_point": {
                "round_id": round_id,
                "standings": [{"team": row["team"], "net_assets": row["net_assets"], "rank": row["rank"]} for row in standings],
            },
        }

    def _actual_market_rows_for_round(self, round_id: str) -> pd.DataFrame:
        rows = self.market_df[self.market_df["round"] == round_id].copy()
        rows["team"] = rows["team"].astype(str)
        rows["final_sales"] = rows["sales_volume"].fillna(0.0)
        rows["simulated_sales_volume"] = rows["final_sales"]
        rows["simulated_marketshare"] = rows["market_share"].fillna(0.0)
        rows["simulated_sales_revenue"] = rows["final_sales"] * rows["price"].fillna(0.0)
        rows["predicted_theoretical_cpi"] = rows.get("market_index", 0.0)
        rows["predicted_marketshare_unconstrained"] = rows["market_share"].fillna(0.0)
        rows["active_market"] = (rows["agents"].fillna(0.0) > 0) | (rows["sales_volume"].fillna(0.0) > 0)
        rows["agents_after"] = rows["agents"].fillna(0).astype(int)
        rows["agents_before"] = 0
        rows["agent_change"] = rows["agents_after"]
        return rows

    def _actual_market_results_for_team(
        self,
        *,
        actual_market_df: pd.DataFrame,
        round_id: str,
        team: str,
        decision: SimulationInput,
        context: dict[str, Any],
        subscribed_markets: list[str],
    ) -> list[dict[str, Any]]:
        subscribed = set(subscribed_markets)
        team_rows = actual_market_df[actual_market_df["team"].astype(str) == str(team)].copy()
        by_market = {str(row["market"]): row for _, row in team_rows.iterrows()}
        results: list[dict[str, Any]] = []
        for market in self.key_data["markets"].keys():
            row = by_market.get(market)
            market_decision = decision.market_decisions[market]
            previous_agents = int(context["market_defaults"].get(market, {}).get("previous_agents", 0) or 0)
            if row is None:
                agents_after = previous_agents + int(market_decision.agent_change)
                price = float(market_decision.price) if agents_after > 0 else 0.0
                marketing = float(market_decision.marketing_investment) if agents_after > 0 else 0.0
                sales_volume = 0.0
                market_share = 0.0
                competitive_power = 0.0
            else:
                agents_after = int(row.get("agents_after", 0) or 0)
                price = float(row.get("price", 0.0) or 0.0)
                marketing = float(row.get("marketing_investment", 0.0) or 0.0)
                sales_volume = float(row.get("sales_volume", 0.0) or 0.0)
                market_share = float(row.get("market_share", 0.0) or 0.0)
                competitive_power = float(row.get("market_index", 0.0) or 0.0)
            results.append(
                {
                    "market": market,
                    "competitive_power": competitive_power,
                    "sales_volume": sales_volume,
                    "market_share": market_share,
                    "price": price,
                    "sales_revenue": sales_volume * price,
                    "agents_before": previous_agents,
                    "agent_change": agents_after - previous_agents,
                    "agents_after": agents_after,
                    "marketing_investment": marketing,
                    "subscribed_market_report": market in subscribed,
                }
            )
        return results

    def _actual_peer_market_tables(self, actual_market_df: pd.DataFrame, subscribed_markets: list[str]) -> dict[str, list[dict[str, Any]]]:
        tables: dict[str, list[dict[str, Any]]] = {}
        for market in subscribed_markets:
            rows = actual_market_df[(actual_market_df["market"] == market) & (actual_market_df["active_market"])].copy()
            rows = rows.sort_values(["market_share", "sales_volume", "team"], ascending=[False, False, True])
            tables[market] = [
                {
                    "team": str(row["team"]),
                    "management_index": float(row.get("management_index", 0.0) or 0.0),
                    "agents": int(row.get("agents", 0) or 0),
                    "marketing_investment": float(row.get("marketing_investment", 0.0) or 0.0),
                    "quality_index": float(row.get("quality_index", 0.0) or 0.0),
                    "price": float(row.get("price", 0.0) or 0.0),
                    "display_sales_volume": float(row.get("sales_volume", 0.0) or 0.0),
                    "sales_volume_exact": float(row.get("sales_volume", 0.0) or 0.0),
                    "predicted_theoretical_cpi": float(row.get("market_index", 0.0) or 0.0),
                    "display_marketshare": float(row.get("market_share", 0.0) or 0.0),
                    "predicted_marketshare_unconstrained": float(row.get("market_share", 0.0) or 0.0),
                }
                for _, row in rows.iterrows()
            ]
        return tables

    def _actual_market_report_summaries(self, actual_market_df: pd.DataFrame, subscribed_markets: list[str]) -> dict[str, dict[str, float]]:
        summaries: dict[str, dict[str, float]] = {}
        for market in subscribed_markets:
            rows = actual_market_df[actual_market_df["market"] == market]
            if rows.empty:
                continue
            first = rows.iloc[0]
            summaries[market] = {
                "population": float(first.get("population", 0.0) or 0.0),
                "penetration": float(first.get("penetration", 0.0) or 0.0),
                "market_size": float(first.get("market_size", 0.0) or 0.0),
                "total_sales_volume": float(first.get("total_sales_volume", 0.0) or 0.0),
                "avg_price": float(first.get("avg_price", 0.0) or 0.0),
            }
        return summaries

    def _state_from_fixed_summary_round(self, round_id: str, team: str, benchmark_worker_avg: float, benchmark_engineer_avg: float) -> CampaignState | None:
        summary_row = self._fixed_round_summary_row(round_id, team)
        if summary_row is None:
            return None
        decision_row = self.fixed_decisions_df[
            (self.fixed_decisions_df["round_id"] == round_id) & (self.fixed_decisions_df["team"].astype(str) == str(team))
        ]
        if decision_row.empty:
            return None
        row = decision_row.iloc[0]
        return CampaignState(
            current_cash=float(summary_row.get("ending_cash_est", 0.0) or 0.0),
            current_debt=float(summary_row.get("ending_debt_est", 0.0) or 0.0),
            workers=int(summary_row.get("workers_est", 0) or 0),
            engineers=int(summary_row.get("engineers_est", 0) or 0),
            worker_salary=float(summary_row.get("worker_salary_est", benchmark_worker_avg) or benchmark_worker_avg),
            engineer_salary=float(summary_row.get("engineer_salary_est", benchmark_engineer_avg) or benchmark_engineer_avg),
            market_agents_after={
                market: int(row.get(f"{market.lower()}_agents_after", 0) or 0) for market in self.key_data["markets"].keys()
            },
            previous_management_index=float(summary_row.get("management_index_source", 0.0) or 0.0),
            previous_quality_index=float(summary_row.get("quality_index_source", 0.0) or 0.0),
            worker_avg_salary=float(benchmark_worker_avg),
            engineer_avg_salary=float(benchmark_engineer_avg),
            worker_recent=0,
            worker_mature=0,
            worker_experienced=int(summary_row.get("workers_est", 0) or 0),
            engineer_recent=0,
            engineer_mature=0,
            engineer_experienced=int(summary_row.get("engineers_est", 0) or 0),
            component_capacity=0.0,
            product_capacity=0.0,
            component_inventory=0.0,
            product_inventory=0.0,
            active_patents=0,
            accumulated_research_investment=0.0,
            last_round_id=round_id,
        )

    def _replay_real_original_report(
        self,
        decision: SimulationInput,
        context: dict[str, Any],
        *,
        mode: str,
    ) -> tuple[dict[str, Any], dict[str, CampaignState | None]]:
        round_id = decision.round_id
        summary_row = self._fixed_round_summary_row(round_id, TEAM_ID)
        if summary_row is None:
            raise ValueError(f"缺少 {round_id} C{TEAM_ID} 原始财报。")
        all_company_results = self._fixed_summary_company_results(round_id)
        actual_market_df = self._actual_market_rows_for_round(round_id)
        subscribed_markets = [
            market
            for market, item in decision.market_decisions.items()
            if item.subscribed_market_report
        ]
        market_results = self._actual_market_results_for_team(
            actual_market_df=actual_market_df,
            round_id=round_id,
            team=TEAM_ID,
            decision=decision,
            context=context,
            subscribed_markets=subscribed_markets,
        )
        sales_agents_table = [
            {
                "market": row["market"],
                "previous": row["agents_before"],
                "change": row["agent_change"],
                "after": row["agents_after"],
                "change_cost": (300_000.0 if row["agent_change"] >= 0 else 100_000.0) * abs(row["agent_change"]),
                "marketing_investment": row["marketing_investment"],
                "subscribed_market_report": row["subscribed_market_report"],
            }
            for row in market_results
        ]
        ending_cash = float(summary_row.get("ending_cash_est", 0.0) or 0.0)
        ending_debt = float(summary_row.get("ending_debt_est", 0.0) or 0.0)
        sales_revenue = float(summary_row.get("sales_revenue_source", 0.0) or 0.0)
        net_assets = ending_cash - ending_debt
        standings = all_company_results["standings"]
        rank = next((row["rank"] for row in standings if str(row["team"]) == TEAM_ID), None)
        report = {
            "round_id": round_id,
            "title": f"{context['title']} 原始财报",
            "key_metrics": {
                "总资产": ending_cash,
                "负债": ending_debt,
                "净资产": net_assets,
                "销售收入": sales_revenue,
                "成本": None,
                "净利润": ending_cash - float(summary_row.get("starting_cash_est", 0.0) or 0.0) - float(summary_row.get("loan_delta_est", 0.0) or 0.0),
                "预计排名": rank,
            },
            "finance_rows": [
                ("期初现金", float(summary_row.get("starting_cash_est", 0.0) or 0.0), None, None, None),
                ("新增贷款", float(summary_row.get("loan_delta_est", 0.0) or 0.0), None, None, None),
                ("销售收入", sales_revenue, None, None, None),
                ("市场报告费用", -float(summary_row.get("market_report_cost_est", 0.0) or 0.0), None, None, None),
                ("期末现金", ending_cash, None, None, None),
                ("期末负债", ending_debt, None, None, None),
            ],
            "hr_summary": [
                {"category": "工人", "working": int(summary_row.get("workers_est", 0) or 0), "salary": float(summary_row.get("worker_salary_est", 0.0) or 0.0)},
                {"category": "工程师", "working": int(summary_row.get("engineers_est", 0) or 0), "salary": float(summary_row.get("engineer_salary_est", 0.0) or 0.0)},
            ],
            "hr_detail": [],
            "management_summary": {"管理指数": float(summary_row.get("management_index_source", 0.0) or 0.0)},
            "production_summary": {
                "计划生产": int(summary_row.get("products_planned_est", 0) or 0),
                "实际销量": float(summary_row.get("sales_units_source", 0.0) or 0.0),
                "产品质量指数": float(summary_row.get("quality_index_source", 0.0) or 0.0),
                "管理指数": float(summary_row.get("management_index_source", 0.0) or 0.0),
            },
            "production_overview": [],
            "production_details": [],
            "storage_summary": [],
            "research_summary": {},
            "market_results": market_results,
            "sales_agents_table": sales_agents_table,
            "peer_market_tables": self._actual_peer_market_tables(actual_market_df, subscribed_markets),
            "market_report_summaries": self._actual_market_report_summaries(actual_market_df, subscribed_markets),
            "notes": ["当前为 real-original 默认决策 replay：财报、排名、市场报表来自原始工作簿，不经过预测模型重算。"],
            "starting_cash": float(summary_row.get("starting_cash_est", 0.0) or 0.0),
            "starting_debt": float(summary_row.get("starting_debt_est", 0.0) or 0.0),
            "ending_cash": ending_cash,
            "ending_debt": ending_debt,
            "leftover_inventory_value": 0.0,
            "total_assets": ending_cash,
            "net_assets": net_assets,
            "net_profit": ending_cash - float(summary_row.get("starting_cash_est", 0.0) or 0.0) - float(summary_row.get("loan_delta_est", 0.0) or 0.0),
            "management_index": float(summary_row.get("management_index_source", 0.0) or 0.0),
            "quality_index": float(summary_row.get("quality_index_source", 0.0) or 0.0),
            "worker_plan": {},
            "engineer_plan": {},
            "component_inventory_end": 0.0,
            "product_inventory_end": 0.0,
            "active_patents_next_round": 0,
            "accumulated_research_investment_next_round": 0.0,
            "all_company_standings": standings,
            "all_company_net_assets_point": all_company_results["net_assets_point"],
            "market_report_subscriptions": subscribed_markets,
            "market_report_source": {"mode": "real-original-replay", "round_id": round_id},
        }
        prev_worker_avg, prev_engineer_avg = self._previous_global_average_salary({team: None for team in self.team_ids})
        decisions = self._round_decisions(round_id)
        benchmark_worker_avg, benchmark_engineer_avg = self._current_global_average_salary_for_multiplayer(
            decisions,
            prev_worker_avg,
            prev_engineer_avg,
        )
        next_states = {
            team: self._state_from_fixed_summary_round(round_id, team, benchmark_worker_avg, benchmark_engineer_avg)
            for team in self.team_ids
        }
        return report, next_states

    def _simulate_multiplayer_report(
        self,
        decision: SimulationInput,
        context: dict[str, Any],
        *,
        mode: str,
        team_states: dict[str, CampaignState | None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, CampaignState | None]]:
        current_home_city = str(context.get("current_home_city", "") or "")
        game_id = str(context.get("game_id", "default"))
        if self._can_replay_real_original_report(decision, context):
            return self._replay_real_original_report(decision, context, mode=mode)
        if team_states is not None:
            states = team_states
        else:
            states = self._states_before_round(
                decision.round_id,
                current_home_city=current_home_city or None,
                game_id=game_id,
            )
            states[TEAM_ID] = self._campaign_state_from_context(context)
        decisions_by_team = self._round_decisions(decision.round_id, team13_decision=decision)
        contexts_by_team = {
            team: self._context_for_company_state(
                decision.round_id,
                team,
                states.get(team),
                current_home_city=current_home_city if team == TEAM_ID else None,
                game_id=game_id,
            )
            for team in decisions_by_team
        }

        errors = self._validate(decision, contexts_by_team[TEAM_ID])
        if errors:
            raise ValueError("\n".join(errors))

        prev_worker_avg, prev_engineer_avg = self._previous_global_average_salary(states)
        benchmark_worker_avg, benchmark_engineer_avg = self._current_global_average_salary_for_multiplayer(
            decisions_by_team,
            prev_worker_avg,
            prev_engineer_avg,
        )
        effective_by_team = {
            team: self._effective_decision_for_team(
                team,
                decisions_by_team[team],
                contexts_by_team[team],
                starting_cash=float(contexts_by_team[team]["starting_cash"]),
                starting_debt=float(contexts_by_team[team]["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )
            for team in decisions_by_team
        }
        full_market_df, team_frames = self._simulate_market_multiplayer(
            effective_decisions_by_team=effective_by_team,
            contexts_by_team=contexts_by_team,
            current_home_city=current_home_city or None,
        )
        outcomes_by_team: dict[str, dict[str, Any]] = {}
        for team, effective_decision in effective_by_team.items():
            team_context = contexts_by_team[team]
            team_market_df = team_frames[team]
            outcomes_by_team[team] = self._financial_outcome_for_team(
                team,
                team_market_df,
                effective_decision,
                team_context,
                float(team_context["starting_cash"]),
                float(team_context["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )

        all_company_results = self._assemble_company_results_from_outcomes(decision.round_id, outcomes_by_team)
        team_context = contexts_by_team[TEAM_ID]
        effective_decision = effective_by_team[TEAM_ID]
        team_market_df = team_frames[TEAM_ID]
        team_financial = outcomes_by_team[TEAM_ID]

        finance_result = build_finance_rows(
            starting_cash=float(team_context["starting_cash"]),
            starting_debt=float(team_context["starting_debt"]),
            loan_delta=float(effective_decision.loan_delta),
            principal_after=float(team_financial["principal_after"]),
            ordered_costs=[
                ("工人工资支出", float(team_financial["workers_salary_cost"])),
                ("工程师工资支出", float(team_financial["engineers_salary_cost"])),
                ("零件材料成本", float(team_financial["component_material_cost"])),
                ("零件仓储成本", float(team_financial["component_storage_cost"])),
                ("成品材料成本", float(team_financial["product_material_cost"])),
                ("成品仓储成本", float(team_financial["product_storage_cost"])),
                ("裁员补偿", float(team_financial["layoff_cost"])),
                ("降薪离职补偿", float(team_financial["salary_reduction_penalty"])),
                ("销售代理调整成本", float(team_financial["agent_change_cost"])),
                ("营销投入", float(team_financial["marketing_investment"])),
                ("质量投入", float(effective_decision.quality_investment)),
                ("管理投入", float(effective_decision.management_investment)),
            ],
            revenue=float(team_financial["revenue"]),
            market_report_cost=float(team_financial["market_report_cost"]),
            research_investment=float(team_financial["research_investment"]),
            interest=float(team_financial["interest"]),
            tax=float(team_financial["tax"]),
        )

        report = assemble_simulation_report(
            decision=decision,
            effective_decision=effective_decision,
            context=team_context,
            team_market_df=team_market_df,
            full_market_df=full_market_df,
            team_financial=team_financial,
            finance_rows=finance_result["finance_rows"],
            all_company_results=all_company_results,
            mode=mode,
        )

        next_states: dict[str, CampaignState | None] = {}
        for team in self.team_ids:
            raw_decision = decisions_by_team.get(team)
            team_context = contexts_by_team.get(team)
            team_market_df = team_frames.get(team)
            outcome = outcomes_by_team.get(team)
            if raw_decision is None or team_context is None or team_market_df is None or outcome is None:
                next_states[team] = states.get(team)
                continue
            report_stub = self._report_stub_for_state_transition(
                context=team_context,
                decision=effective_by_team[team],
                outcome=outcome,
                team_market_df=team_market_df,
            )
            next_state = next_campaign_state(report=report_stub, decision=raw_decision, state=states.get(team))
            next_states[team] = CampaignState(
                current_cash=next_state.current_cash,
                current_debt=next_state.current_debt,
                workers=next_state.workers,
                engineers=next_state.engineers,
                worker_salary=next_state.worker_salary,
                engineer_salary=next_state.engineer_salary,
                market_agents_after=next_state.market_agents_after,
                previous_management_index=next_state.previous_management_index,
                previous_quality_index=next_state.previous_quality_index,
                worker_avg_salary=float(benchmark_worker_avg),
                engineer_avg_salary=float(benchmark_engineer_avg),
                worker_recent=next_state.worker_recent,
                worker_mature=next_state.worker_mature,
                worker_experienced=next_state.worker_experienced,
                engineer_recent=next_state.engineer_recent,
                engineer_mature=next_state.engineer_mature,
                engineer_experienced=next_state.engineer_experienced,
                component_capacity=next_state.component_capacity,
                product_capacity=next_state.product_capacity,
                component_inventory=next_state.component_inventory,
                product_inventory=next_state.product_inventory,
                active_patents=next_state.active_patents,
                accumulated_research_investment=next_state.accumulated_research_investment,
                last_round_id=next_state.last_round_id,
            )
        return report, next_states

    def simulate_room_round(
        self,
        *,
        round_id: str,
        human_decisions_by_team: dict[str, SimulationInput],
        human_team_ids: list[str],
        team_states: dict[str, CampaignState | None] | None = None,
        game_id: str | None = None,
        mode: str = "multiplayer",
        participant_team_ids: list[str] | None = None,
        current_home_city_by_team: dict[str, str] | None = None,
        use_historical_initial_state: bool = True,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, CampaignState | None], dict[str, Any]]:
        mutable_teams = {str(team).strip() for team in human_team_ids if str(team).strip()}
        if not mutable_teams:
            raise ValueError("多人房间至少需要一个真人席位。")
        home_city_overrides = {
            str(team).strip(): str(city).strip()
            for team, city in (current_home_city_by_team or {}).items()
            if str(team).strip() and str(city).strip()
        }
        states = (
            team_states
            if team_states is not None
            else self._states_before_round(
                round_id,
                game_id=game_id,
            )
        )
        decisions_by_team = self._round_decisions_with_overrides(
            round_id,
            decision_overrides_by_team=human_decisions_by_team,
        )
        participating_teams = [
            str(team).strip()
            for team in (participant_team_ids or decisions_by_team.keys())
            if str(team).strip() in decisions_by_team
        ]
        if not participating_teams:
            raise ValueError("多人房间缺少有效参赛队伍。")
        decisions_by_team = {team: decisions_by_team[team] for team in participating_teams}
        contexts_by_team = {
            team: self._context_for_company_state(
                round_id,
                team,
                states.get(team),
                current_home_city=home_city_overrides.get(str(team).strip()) or None,
                game_id=game_id,
                use_historical_initial_state=use_historical_initial_state,
            )
            for team in decisions_by_team
        }
        validation_errors: list[str] = []
        for team in sorted(mutable_teams, key=lambda value: int(value) if value.isdigit() else value):
            decision = decisions_by_team.get(team)
            if decision is None:
                validation_errors.append(f"队伍 {team} 缺少当前轮次输入。")
                continue
            errors = self._validate(decision, contexts_by_team[team])
            validation_errors.extend([f"队伍 {team}：{message}" for message in errors])
        if validation_errors:
            raise ValueError("\n".join(validation_errors))

        prev_worker_avg, prev_engineer_avg = self._previous_global_average_salary(states)
        benchmark_worker_avg, benchmark_engineer_avg = self._current_global_average_salary_for_multiplayer(
            decisions_by_team,
            prev_worker_avg,
            prev_engineer_avg,
        )
        effective_by_team = {
            team: self._effective_decision_for_multiplayer_team(
                team,
                decisions_by_team[team],
                contexts_by_team[team],
                mutable_teams=mutable_teams,
                starting_cash=float(contexts_by_team[team]["starting_cash"]),
                starting_debt=float(contexts_by_team[team]["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )
            for team in decisions_by_team
        }
        full_market_df, team_frames = self._simulate_market_multiplayer(
            effective_decisions_by_team=effective_by_team,
            contexts_by_team=contexts_by_team,
        )
        outcomes_by_team: dict[str, dict[str, Any]] = {}
        for team, effective_decision in effective_by_team.items():
            team_context = contexts_by_team[team]
            team_market_df = team_frames[team]
            outcomes_by_team[team] = self._financial_outcome_for_team(
                team,
                team_market_df,
                effective_decision,
                team_context,
                float(team_context["starting_cash"]),
                float(team_context["starting_debt"]),
                benchmark_worker_avg=benchmark_worker_avg,
                benchmark_engineer_avg=benchmark_engineer_avg,
            )
        all_company_results = self._assemble_company_results_from_outcomes(round_id, outcomes_by_team)
        reports_by_team: dict[str, dict[str, Any]] = {}
        for team in mutable_teams:
            decision = decisions_by_team[team]
            effective_decision = effective_by_team[team]
            team_context = contexts_by_team[team]
            team_market_df = team_frames[team]
            team_financial = outcomes_by_team[team]
            finance_result = build_finance_rows(
                starting_cash=float(team_context["starting_cash"]),
                starting_debt=float(team_context["starting_debt"]),
                loan_delta=float(effective_decision.loan_delta),
                principal_after=float(team_financial["principal_after"]),
                ordered_costs=[
                    ("工人工资支出", float(team_financial["workers_salary_cost"])),
                    ("工程师工资支出", float(team_financial["engineers_salary_cost"])),
                    ("零件材料成本", float(team_financial["component_material_cost"])),
                    ("零件仓储成本", float(team_financial["component_storage_cost"])),
                    ("成品材料成本", float(team_financial["product_material_cost"])),
                    ("成品仓储成本", float(team_financial["product_storage_cost"])),
                    ("裁员补偿", float(team_financial["layoff_cost"])),
                    ("降薪离职补偿", float(team_financial["salary_reduction_penalty"])),
                    ("销售代理调整成本", float(team_financial["agent_change_cost"])),
                    ("营销投入", float(team_financial["marketing_investment"])),
                    ("质量投入", float(effective_decision.quality_investment)),
                    ("管理投入", float(effective_decision.management_investment)),
                ],
                revenue=float(team_financial["revenue"]),
                market_report_cost=float(team_financial["market_report_cost"]),
                research_investment=float(team_financial["research_investment"]),
                interest=float(team_financial["interest"]),
                tax=float(team_financial["tax"]),
            )
            reports_by_team[team] = assemble_simulation_report(
                decision=decision,
                effective_decision=effective_decision,
                context=team_context,
                team_market_df=team_market_df,
                full_market_df=full_market_df,
                team_financial=team_financial,
                finance_rows=finance_result["finance_rows"],
                all_company_results=all_company_results,
                mode=mode,
            )

        next_states: dict[str, CampaignState | None] = {}
        for team in self.team_ids:
            raw_decision = decisions_by_team.get(team)
            team_context = contexts_by_team.get(team)
            team_market_df = team_frames.get(team)
            outcome = outcomes_by_team.get(team)
            if raw_decision is None or team_context is None or team_market_df is None or outcome is None:
                next_states[team] = states.get(team)
                continue
            report_stub = self._report_stub_for_state_transition(
                context=team_context,
                decision=effective_by_team[team],
                outcome=outcome,
                team_market_df=team_market_df,
            )
            next_state = next_campaign_state(report=report_stub, decision=raw_decision, state=states.get(team))
            next_states[team] = CampaignState(
                current_cash=next_state.current_cash,
                current_debt=next_state.current_debt,
                workers=next_state.workers,
                engineers=next_state.engineers,
                worker_salary=next_state.worker_salary,
                engineer_salary=next_state.engineer_salary,
                market_agents_after=next_state.market_agents_after,
                previous_management_index=next_state.previous_management_index,
                previous_quality_index=next_state.previous_quality_index,
                worker_avg_salary=float(benchmark_worker_avg),
                engineer_avg_salary=float(benchmark_engineer_avg),
                worker_recent=next_state.worker_recent,
                worker_mature=next_state.worker_mature,
                worker_experienced=next_state.worker_experienced,
                engineer_recent=next_state.engineer_recent,
                engineer_mature=next_state.engineer_mature,
                engineer_experienced=next_state.engineer_experienced,
                component_capacity=next_state.component_capacity,
                product_capacity=next_state.product_capacity,
                component_inventory=next_state.component_inventory,
                product_inventory=next_state.product_inventory,
                active_patents=next_state.active_patents,
                accumulated_research_investment=next_state.accumulated_research_investment,
                last_round_id=next_state.last_round_id,
            )
        return reports_by_team, next_states, all_company_results

    def _simulate_with_context(self, decision: SimulationInput, context: dict[str, Any], *, mode: str) -> dict[str, Any]:
        report, _ = self._simulate_multiplayer_report(decision, context, mode=mode)
        return report

    def simulate(self, decision: SimulationInput) -> dict[str, Any]:
        context = self._context_with_campaign_state(decision.round_id, None)
        return self._simulate_with_context(decision, context, mode="single")

    def _next_campaign_state(self, report: dict[str, Any], decision: SimulationInput, state: CampaignState | None) -> CampaignState:
        return next_campaign_state(report=report, decision=decision, state=state)

    def simulate_campaign(self, campaign: CampaignSimulationInput) -> dict[str, Any]:
        missing_rounds = [round_id for round_id in self.available_rounds() if round_id not in campaign.rounds]
        if missing_rounds:
            raise ValueError(f"缺少以下轮次的决策：{', '.join(missing_rounds)}")

        round_reports = []
        team_states: dict[str, CampaignState | None] = {team: None for team in self.team_ids}
        for round_id in self.available_rounds():
            decision = campaign.rounds[round_id]
            context = self._context_with_campaign_state(round_id, team_states.get(TEAM_ID))
            report, team_states = self._simulate_multiplayer_report(
                decision,
                context,
                mode="campaign",
                team_states=team_states,
            )
            round_reports.append(report)

        campaign_summary = {
            "总营收": float(sum(report["key_metrics"]["销售收入"] for report in round_reports)),
            "总净利润": float(sum(report["net_profit"] for report in round_reports)),
            "期末现金": float(round_reports[-1]["ending_cash"]),
            "期末负债": float(round_reports[-1]["ending_debt"]),
            "期末净资产": float(round_reports[-1]["key_metrics"]["净资产"]),
        }

        return {
            "title": "Exschool 四轮经营总结",
            "summary": campaign_summary,
            "reports": round_reports,
            "notes": [
                "系统会按 r1 到 r4 的顺序依次执行每一轮模拟。",
                "每一轮都会从上一轮模拟得到的期末现金、负债、人员、工资和渠道基础继续开始。",
                "所有队伍都会按同一套规则逐轮推进；Team 13 使用当前提交的决策，其它队伍使用固定输入决策。",
            ],
        }


@lru_cache(maxsize=4)
def get_simulator(single_player_mode: str = "high-intensity") -> ExschoolSimulator:
    return ExschoolSimulator(single_player_mode=normalize_fixed_decision_mode(single_player_mode))
