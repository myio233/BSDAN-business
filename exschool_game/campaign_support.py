from __future__ import annotations

from typing import Any

from .models import CampaignState, MarketDecision, SimulationInput


def _value_or_default(container: dict[str, Any], key: str, default: Any) -> Any:
    value = container.get(key, default)
    return default if value is None else value


def build_simulation_input(
    *,
    visible_markets: set[str],
    round_id: str,
    raw: dict[str, Any],
    context: dict[str, Any] | None = None,
    headcount_is_delta: bool = False,
) -> SimulationInput:
    market_decisions = {
        market: MarketDecision(
            agent_change=int(values["agent_change"]),
            marketing_investment=float(values["marketing_investment"]),
            price=float(values["price"]),
            subscribed_market_report=bool(values.get("subscribed_market_report", True)),
        )
        for market, values in raw["markets"].items()
        if market in visible_markets
    }
    workers_value = int(raw["workers"])
    engineers_value = int(raw["engineers"])
    if headcount_is_delta:
        previous_workers = int((context or {}).get("workers_actual", 0) or 0)
        previous_engineers = int((context or {}).get("engineers_actual", 0) or 0)
        workers_value = max(previous_workers + workers_value, 0)
        engineers_value = max(previous_engineers + engineers_value, 0)
    return SimulationInput(
        round_id=round_id,
        loan_delta=float(raw["loan_delta"]),
        workers=workers_value,
        engineers=engineers_value,
        worker_salary=float(raw["worker_salary"]),
        engineer_salary=float(raw["engineer_salary"]),
        management_investment=float(raw["management_investment"]),
        quality_investment=float(raw["quality_investment"]),
        research_investment=float(raw.get("research_investment", 0.0)),
        products_planned=int(raw["products_planned"]),
        market_decisions=market_decisions,
    )


def payload_for_context(round_id: str, context: dict[str, Any]) -> dict[str, Any]:
    def default_market_report_subscription(market_name: str) -> bool:
        market_defaults = context["market_defaults"][market_name]
        if "payload_subscribed_market_report" in market_defaults:
            return bool(market_defaults.get("payload_subscribed_market_report"))
        previous_agents = int(market_defaults.get("previous_agents", 0) or 0)
        actual_sales_volume = float(market_defaults.get("actual_sales_volume", 0.0) or 0.0)
        return previous_agents > 0 or actual_sales_volume > 0

    return {
        "round_id": round_id,
        "loan_delta": float(_value_or_default(context, "payload_loan_delta", context.get("actual_loan_delta", 0.0)) or 0.0),
        "workers": int(context.get("payload_workers", context["workers_actual"])) - int(context.get("workers_actual", 0)),
        "engineers": int(context.get("payload_engineers", context["engineers_actual"])) - int(context.get("engineers_actual", 0)),
        "worker_salary": float(_value_or_default(context, "payload_worker_salary", context.get("worker_salary_actual", 0.0)) or 0.0),
        "engineer_salary": float(_value_or_default(context, "payload_engineer_salary", context.get("engineer_salary_actual", 0.0)) or 0.0),
        "management_investment": float(
            _value_or_default(context, "payload_management_investment", context.get("management_investment_actual", 0.0)) or 0.0
        ),
        "quality_investment": float(
            _value_or_default(context, "payload_quality_investment", context.get("quality_investment_actual", 0.0)) or 0.0
        ),
        "research_investment": float(
            _value_or_default(context, "payload_research_investment", context.get("research_investment_actual", 0.0)) or 0.0
        ),
        "products_planned": int(_value_or_default(context, "payload_products_planned", context.get("products_produced_actual", 0)) or 0),
        "markets": {
            market: {
                "agent_change": int(
                    _value_or_default(context["market_defaults"][market], "payload_agent_change", context["market_defaults"][market].get("actual_change", 0))
                    or 0
                ),
                "marketing_investment": float(
                    _value_or_default(
                        context["market_defaults"][market],
                        "payload_marketing_investment",
                        context["market_defaults"][market].get("actual_marketing_investment", 0.0),
                    )
                    or 0.0
                ),
                "price": float(
                    _value_or_default(
                        context["market_defaults"][market],
                        "payload_price",
                        context["market_defaults"][market].get("actual_price", 0.0),
                    )
                    or 0.0
                ),
                "subscribed_market_report": context["market_defaults"][market].get(
                    "payload_subscribed_market_report",
                    default_market_report_subscription(market),
                ),
            }
            for market in context["visible_markets"]
        },
    }


def build_campaign_state(
    *,
    context: dict[str, Any],
    initial_worker_avg: float,
    initial_engineer_avg: float,
) -> CampaignState:
    total_people = context["workers_actual"] + context["engineers_actual"]
    management_index = context["management_investment_actual"] / total_people if total_people > 0 else 0.0
    quality_index = (
        context["quality_investment_actual"] / context["products_produced_actual"]
        if context["products_produced_actual"] > 0
        else 0.0
    )
    return CampaignState(
        current_cash=context["starting_cash"],
        current_debt=context["starting_debt"],
        workers=context["workers_actual"],
        engineers=context["engineers_actual"],
        worker_salary=context["worker_salary_actual"],
        engineer_salary=context["engineer_salary_actual"],
        market_agents_after={
            market: int(context["market_defaults"][market]["previous_agents"]) for market in context["visible_markets"]
        },
        previous_management_index=management_index,
        previous_quality_index=quality_index,
        worker_avg_salary=float(initial_worker_avg),
        engineer_avg_salary=float(initial_engineer_avg),
        worker_recent=int(context["workers_actual"]),
        worker_mature=0,
        worker_experienced=0,
        engineer_recent=int(context["engineers_actual"]),
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


def next_campaign_state(
    *,
    report: dict[str, Any],
    decision: SimulationInput,
    state: CampaignState | None,
) -> CampaignState:
    worker_plan = report.get("worker_plan", {})
    engineer_plan = report.get("engineer_plan", {})
    previous_market_agents = state.market_agents_after if state is not None else {}
    return CampaignState(
        current_cash=float(report["ending_cash"]),
        current_debt=float(report["ending_debt"]),
        workers=int(worker_plan.get("working", decision.workers)),
        engineers=int(engineer_plan.get("working", decision.engineers)),
        worker_salary=decision.worker_salary,
        engineer_salary=decision.engineer_salary,
        market_agents_after={
            **previous_market_agents,
            **{row["market"]: int(row["agents_after"]) for row in report["market_results"]},
        },
        previous_management_index=float(report["management_index"]),
        previous_quality_index=float(report["quality_index"]),
        worker_avg_salary=float(worker_plan.get("average_salary", decision.worker_salary)),
        engineer_avg_salary=float(engineer_plan.get("average_salary", decision.engineer_salary)),
        worker_recent=int(worker_plan.get("next_recent", decision.workers)),
        worker_mature=int(worker_plan.get("next_mature", 0)),
        worker_experienced=int(worker_plan.get("next_experienced", 0)),
        engineer_recent=int(engineer_plan.get("next_recent", decision.engineers)),
        engineer_mature=int(engineer_plan.get("next_mature", 0)),
        engineer_experienced=int(engineer_plan.get("next_experienced", 0)),
        component_capacity=float(next((row["capacity_after"] for row in report["storage_summary"] if row["item"] == "Components"), 0.0)),
        product_capacity=float(next((row["capacity_after"] for row in report["storage_summary"] if row["item"] == "Products"), 0.0)),
        component_inventory=float(report.get("component_inventory_end", 0.0)),
        product_inventory=float(report.get("product_inventory_end", 0.0)),
        active_patents=int(report.get("active_patents_next_round", 0)),
        accumulated_research_investment=float(report.get("accumulated_research_investment_next_round", 0.0)),
        last_round_id=decision.round_id,
    )
