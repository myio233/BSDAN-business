from __future__ import annotations

from typing import Any

import pandas as pd

from .models import SimulationInput
from .research import patent_cost_multiplier


def build_market_results(
    *,
    team_market_df: pd.DataFrame,
    decision: SimulationInput,
    market_defaults: dict[str, dict[str, Any]],
    subscribed_markets: list[str],
) -> list[dict[str, Any]]:
    market_results = []
    subscribed_set = set(subscribed_markets)
    for _, row in team_market_df.sort_values("market").iterrows():
        market_results.append(
            {
                "market": row["market"],
                "competitive_power": row["predicted_theoretical_cpi"],
                "sales_volume": row["simulated_sales_volume"],
                "market_share": row["simulated_marketshare"],
                "price": row["price"],
                "sales_revenue": row["simulated_sales_revenue"],
                "agents_before": row["agents_before"],
                "agent_change": row["agent_change"],
                "agents_after": row["agents_after"],
                "marketing_investment": row["marketing_investment"],
                "subscribed_market_report": row["market"] in subscribed_set,
            }
        )
    return market_results


def build_peer_market_tables(full_market_df: pd.DataFrame, subscribed_markets: list[str]) -> dict[str, list[dict[str, Any]]]:
    peer_market_tables: dict[str, list[dict[str, Any]]] = {}
    for market in subscribed_markets:
        peer_rows = full_market_df[
            (full_market_df["market"] == market) & (full_market_df["active_market"].fillna(False))
        ].copy()
        peer_rows["display_marketshare"] = (
            peer_rows["final_sales"] / peer_rows["market_size"]
        ).where(peer_rows["market_size"] > 0, 0.0)
        peer_rows["display_sales_volume"] = peer_rows["final_sales"]
        peer_rows["team_sort"] = peer_rows["team"].astype(str)
        peer_market_tables[market] = peer_rows.sort_values(
            ["display_marketshare", "display_sales_volume", "predicted_theoretical_cpi", "team_sort"],
            ascending=[False, False, False, True],
            kind="mergesort",
        )[
            [
                "team",
                "management_index",
                "agents",
                "marketing_investment",
                "quality_index",
                "price",
                "display_sales_volume",
                "final_sales",
                "predicted_theoretical_cpi",
                "display_marketshare",
                "predicted_marketshare_unconstrained",
            ]
        ].to_dict(orient="records")
        for row in peer_market_tables[market]:
            row["team"] = str(row["team"])
            row["sales_volume_exact"] = row.get("final_sales", row.get("display_sales_volume", 0.0))
            row.pop("final_sales", None)
    return peer_market_tables


def build_market_report_summaries(
    full_market_df: pd.DataFrame,
    subscribed_markets: list[str],
) -> dict[str, dict[str, float]]:
    summaries: dict[str, dict[str, float]] = {}
    for market in subscribed_markets:
        market_rows = full_market_df[full_market_df["market"] == market].copy()
        if market_rows.empty:
            continue
        active_rows = market_rows[market_rows["active_market"].fillna(False)].copy()
        summary_rows = active_rows if not active_rows.empty else market_rows
        total_sales_volume = float(active_rows["final_sales"].fillna(0.0).sum()) if not active_rows.empty else 0.0
        if total_sales_volume > 0:
            avg_price = float(
                (active_rows["price"].fillna(0.0) * active_rows["final_sales"].fillna(0.0)).sum() / total_sales_volume
            )
        else:
            avg_price = float(summary_rows["price"].fillna(0.0).mean()) if "price" in summary_rows else 0.0
        first_row = summary_rows.iloc[0]
        summaries[market] = {
            "population": float(first_row.get("population", 0.0) or 0.0),
            "penetration": float(first_row.get("penetration", 0.0) or 0.0),
            "market_size": float(first_row.get("market_size", 0.0) or 0.0),
            "total_sales_volume": total_sales_volume,
            "avg_price": avg_price,
        }
    return summaries


def build_report_notes(mode: str, current_team_id: str = "13") -> list[str]:
    controlled_team_label = f"当前队伍 C{current_team_id}"
    notes = [
        f"所选轮次中，所有队伍都会按统一规则从各自决策出发参与同一轮竞争；Team {current_team_id} 使用当前输入，当前展示的是 {controlled_team_label} 的真实结算结果。",
        f"当前模型已把主场城市是否等于当前市场作为额外特征，因此你在开局页选择的主场城市会影响 {controlled_team_label} 的 CPI 与份额预测。",
        "每个市场的销量按以下规则分配：先用 CPI 模型算出每支队伍的理论竞争力，再结合价格/管理/市场/质量相对强弱与市场饱和度，把理论 CPI 映射成理论份额；"
        "再以 min(货量, 理论份额对应销量) 作为初步销量，所有参赛队伍都会按各自该轮 products_planned 在所选市场之间按理论份额需求比例分摊货量；"
        "若某队缺少 products_planned 记录，才退回用历史销量做代理。",
        "若某队伍的货量不足以覆盖自己的理论需求而另一队伍有富余货量，且富余一方的管理指数、市场指数或质量指数中任意一项高于对方，富余方会把对方的空缺吃掉。",
        "质量指数按“质量投入 / (旧成品 × 1.20 + 新成品)”计算；未售出的产品当前仍按 0 价值处理，不计入总资产或净资产。",
        "研发投入会先累计，再按 KDS 拟合出的概率函数触发专利；新专利从下一轮开始降低材料成本。市场报告费用按订阅城市数 × ¥200,000 计算，未订阅城市不会显示市场报表。",
        "若现金在前序成本后已经用尽，后续营销、质量、管理投入会被自动截断，现金不会继续跌到 0 以下。",
    ]
    if mode.startswith("multiplayer"):
        notes[0] = f"当前多人房间只会结算本房间已入局的队伍；这里展示的是 {controlled_team_label} 在本房间中的真实结算结果。"
        notes[2] = (
            "每个市场的销量按以下规则分配：先用 CPI 模型算出当前房间所有参赛队伍的理论竞争力，再结合价格/管理/市场/质量相对强弱与市场饱和度，把理论 CPI 映射成理论份额；"
            "再以 min(货量, 理论份额对应销量) 作为初步销量，所有参赛队伍都会按各自该轮 products_planned 在所选市场之间按理论份额需求比例分摊货量；"
            "若某队缺少 products_planned 记录，才退回用历史销量做代理。"
        )
    if mode == "campaign":
        notes[2] = "多轮模式会延续现金、负债、人员、工资、渠道基础、产品/零件库存、仓储容量以及专利状态；Team 13 的滞后指数也会沿用上一轮模拟结果。"
        notes.append("后续轮次中 Team 13 的滞后特征取自上一轮模拟结果，而不是历史工作簿。")
    return notes


def assemble_simulation_report(
    *,
    decision: SimulationInput,
    effective_decision: SimulationInput,
    context: dict[str, Any],
    team_market_df: pd.DataFrame,
    full_market_df: pd.DataFrame,
    team_financial: dict[str, Any],
    finance_rows: list[tuple[str, float | None, float | None, float | None, float | None]],
    all_company_results: dict[str, Any],
    mode: str,
) -> dict[str, Any]:
    revenue = float(team_financial["revenue"])
    sold_units = float(team_financial["sold_units"])
    leftover_products = float(team_financial["leftover_products"])
    leftover_components = float(team_financial["leftover_components"])
    previous_products = float(team_financial["previous_product_inventory"])
    previous_components = float(team_financial["previous_component_inventory"])
    new_products = float(team_financial["new_products"])
    new_components = float(team_financial["new_components"])
    total_products_available = float(team_financial["total_products_available"])
    total_components_available = float(team_financial["total_components_available"])
    components_used = float(team_financial["components_used"])
    component_material_cost = float(team_financial["component_material_cost"])
    product_material_cost = float(team_financial["product_material_cost"])
    component_storage_cost = float(team_financial["component_storage_cost"])
    product_storage_cost = float(team_financial["product_storage_cost"])
    research_investment = float(team_financial["research_investment"])
    research_probability = float(team_financial["research_probability"])
    patents_added = int(team_financial["patents_added"])
    active_patents_before = int(team_financial["active_patents_before"])
    active_patents_after = int(team_financial["active_patents_after"])
    accumulated_research_prev = float(team_financial["accumulated_research_prev"])
    accumulated_research_next = float(team_financial["accumulated_research_next"])
    ending_cash = float(team_financial["ending_cash"])
    leftover_inventory_value = float(team_financial["leftover_inventory_value"])
    total_assets = float(team_financial["total_assets"])
    ending_debt = float(team_financial["ending_debt"])
    net_assets = float(team_financial["net_assets"])
    net_profit = float(team_financial["net_profit"])
    worker_plan = team_financial["worker_plan"]
    engineer_plan = team_financial["engineer_plan"]
    worker_capacity_detail = dict(team_financial.get("worker_capacity_detail", {}))
    engineer_capacity_detail = dict(team_financial.get("engineer_capacity_detail", {}))
    realized_subscribed_markets = list(team_financial.get("realized_subscribed_markets", []))

    current_team_id = str(context.get("team_id", "13") or "13")
    our_rank = next((row["rank"] for row in all_company_results["standings"] if str(row["team"]) == current_team_id), None)
    key_metrics = {
        "总资产": total_assets,
        "负债": ending_debt,
        "净资产": net_assets,
        "销售收入": revenue,
        "成本": revenue - net_profit,
        "净利润": net_profit,
        "预计排名": our_rank,
    }
    hr_summary = [
        {"category": "工人", "working": effective_decision.workers, "salary": effective_decision.worker_salary},
        {"category": "工程师", "working": effective_decision.engineers, "salary": effective_decision.engineer_salary},
    ]
    hr_detail = [
        {
            "category": "Workers",
            "previous": worker_plan["previous_inexperienced"],
            "added": worker_plan["added"],
            "laid_off": worker_plan["laid_off"],
            "quits": worker_plan["quits"],
            "promotion_ready": worker_plan["promotion_ready"],
            "promoted_this_round": worker_plan["promoted_this_round"],
            "previous_experienced": worker_plan["previous_experienced"],
            "experienced": worker_plan["working_experienced"],
            "working": worker_plan["working_inexperienced"],
            "working_total": effective_decision.workers,
            "salary": effective_decision.worker_salary,
            "avg": worker_plan["average_salary"],
            "salary_ratio": worker_capacity_detail.get("salary_ratio", 1.0),
            "productivity_multiplier": worker_capacity_detail.get("productivity_multiplier", 1.0),
        },
        {
            "category": "Engineers",
            "previous": engineer_plan["previous_inexperienced"],
            "added": engineer_plan["added"],
            "laid_off": engineer_plan["laid_off"],
            "quits": engineer_plan["quits"],
            "promotion_ready": engineer_plan["promotion_ready"],
            "promoted_this_round": engineer_plan["promoted_this_round"],
            "previous_experienced": engineer_plan["previous_experienced"],
            "experienced": engineer_plan["working_experienced"],
            "working": engineer_plan["working_inexperienced"],
            "working_total": effective_decision.engineers,
            "salary": effective_decision.engineer_salary,
            "avg": engineer_plan["average_salary"],
            "salary_ratio": engineer_capacity_detail.get("salary_ratio", 1.0),
            "productivity_multiplier": engineer_capacity_detail.get("productivity_multiplier", 1.0),
        },
    ]
    components_capacity = float(worker_capacity_detail.get("theoretical_capacity", 0.0) or 0.0)
    products_capacity = float(engineer_capacity_detail.get("theoretical_capacity", 0.0) or 0.0)
    production_summary = {
        "计划生产数量": decision.products_planned,
        "实际售出数量": sold_units,
        "库存剩余成品": leftover_products,
        "库存剩余零件": leftover_components,
        "所需零件数": max(float(decision.products_planned) * 7.0 - previous_components, 0.0),
        "零件最大产能": components_capacity,
        "成品最大产能": products_capacity,
        "零件工资倍率": float(worker_capacity_detail.get("productivity_multiplier", 1.0) or 1.0),
        "成品工资倍率": float(engineer_capacity_detail.get("productivity_multiplier", 1.0) or 1.0),
        "旧成品": previous_products,
        "新成品": new_products,
        "质量投资": effective_decision.quality_investment,
        "产品质量指数": effective_decision.quality_investment / (previous_products * 1.2 + new_products) if (previous_products * 1.2 + new_products) > 0 else 0.0,
        "管理指数": effective_decision.management_investment / (effective_decision.workers + effective_decision.engineers) if (effective_decision.workers + effective_decision.engineers) else 0.0,
    }
    production_overview = [
        {
            "item": "Components",
            "plan": max(float(decision.products_planned) * 7.0 - previous_components, 0.0),
            "previous": previous_components,
            "produced": new_components,
            "total": total_components_available,
            "used_sold": components_used,
            "surplus": leftover_components,
        },
        {
            "item": "Products",
            "plan": decision.products_planned,
            "previous": previous_products,
            "produced": new_products,
            "total": total_products_available,
            "used_sold": sold_units,
            "surplus": leftover_products,
        },
    ]
    production_details = [
        {
            "item": "Components",
            "base_productivity": float(worker_capacity_detail.get("base_productivity", context["components_productivity"]) or 0.0),
            "reference_productivity": float(worker_capacity_detail.get("reference_productivity", context["components_productivity"]) or 0.0),
            "salary": float(worker_capacity_detail.get("salary", effective_decision.worker_salary) or 0.0),
            "benchmark_salary": float(worker_capacity_detail.get("benchmark_salary", worker_plan["average_salary"]) or 0.0),
            "salary_ratio": float(worker_capacity_detail.get("salary_ratio", 1.0) or 0.0),
            "productivity_multiplier": float(worker_capacity_detail.get("productivity_multiplier", 1.0) or 0.0),
            "productivity": float(worker_capacity_detail.get("adjusted_productivity", context["components_productivity"]) or 0.0),
            "employees": float(worker_capacity_detail.get("employees", effective_decision.workers) or 0.0),
            "theoretical_capacity": components_capacity,
            "production": new_components,
            "material_price": float(context["component_material_price"]) * patent_cost_multiplier(active_patents_before),
            "material_cost": component_material_cost,
            "experienced": worker_plan["experienced"],
        },
        {
            "item": "Products",
            "base_productivity": float(engineer_capacity_detail.get("base_productivity", context["products_productivity"]) or 0.0),
            "reference_productivity": float(engineer_capacity_detail.get("reference_productivity", context["products_productivity"]) or 0.0),
            "salary": float(engineer_capacity_detail.get("salary", effective_decision.engineer_salary) or 0.0),
            "benchmark_salary": float(engineer_capacity_detail.get("benchmark_salary", engineer_plan["average_salary"]) or 0.0),
            "salary_ratio": float(engineer_capacity_detail.get("salary_ratio", 1.0) or 0.0),
            "productivity_multiplier": float(engineer_capacity_detail.get("productivity_multiplier", 1.0) or 0.0),
            "productivity": float(engineer_capacity_detail.get("adjusted_productivity", context["products_productivity"]) or 0.0),
            "employees": float(engineer_capacity_detail.get("employees", effective_decision.engineers) or 0.0),
            "theoretical_capacity": products_capacity,
            "production": new_products,
            "material_price": float(context["product_material_price"]) * patent_cost_multiplier(active_patents_before),
            "material_cost": product_material_cost,
            "experienced": engineer_plan["experienced"],
        },
    ]
    storage_summary = [
        {
            "item": "Components",
            "capacity_before": float(context.get("component_capacity_prev", 0.0) or 0.0),
            "capacity_after": max(float(context.get("component_capacity_prev", 0.0) or 0.0), total_components_available),
            "increment": team_financial["component_storage_increment"],
            "unit_price": float(context.get("component_storage_unit_cost", 0.0)),
            "storage_cost": component_storage_cost,
        },
        {
            "item": "Products",
            "capacity_before": float(context.get("product_capacity_prev", 0.0) or 0.0),
            "capacity_after": max(float(context.get("product_capacity_prev", 0.0) or 0.0), total_products_available),
            "increment": team_financial["product_storage_increment"],
            "unit_price": float(context.get("product_storage_unit_cost", 0.0)),
            "storage_cost": product_storage_cost,
        },
    ]
    management_summary = {
        "planned_investment": decision.management_investment,
        "investment": effective_decision.management_investment,
        "index": production_summary["管理指数"],
    }
    research_summary = {
        "investment": research_investment,
        "probability": research_probability,
        "previous": active_patents_before,
        "change": patents_added,
        "after": active_patents_after,
        "accumulated_previous": accumulated_research_prev,
        "accumulated": accumulated_research_next,
        "patents": active_patents_after,
    }
    market_results = build_market_results(
        team_market_df=team_market_df,
        decision=decision,
        market_defaults=context["market_defaults"],
        subscribed_markets=realized_subscribed_markets,
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

    return {
        "round_id": effective_decision.round_id,
        "title": f"{context['title']} 模拟结果",
        "key_metrics": key_metrics,
        "finance_rows": finance_rows,
        "hr_summary": hr_summary,
        "hr_detail": hr_detail,
        "management_summary": management_summary,
        "production_summary": production_summary,
        "production_overview": production_overview,
        "production_details": production_details,
        "storage_summary": storage_summary,
        "research_summary": research_summary,
        "market_results": market_results,
        "sales_agents_table": sales_agents_table,
        "peer_market_tables": build_peer_market_tables(full_market_df, realized_subscribed_markets),
        "market_report_summaries": build_market_report_summaries(full_market_df, realized_subscribed_markets),
        "notes": build_report_notes(mode, current_team_id),
        "starting_cash": context["starting_cash"],
        "starting_debt": context["starting_debt"],
        "ending_cash": ending_cash,
        "ending_debt": ending_debt,
        "leftover_inventory_value": leftover_inventory_value,
        "total_assets": total_assets,
        "net_assets": net_assets,
        "net_profit": net_profit,
        "management_index": production_summary["管理指数"],
        "quality_index": production_summary["产品质量指数"],
        "worker_plan": worker_plan,
        "engineer_plan": engineer_plan,
        "component_inventory_end": leftover_components,
        "product_inventory_end": leftover_products,
        "active_patents_next_round": active_patents_after,
        "accumulated_research_investment_next_round": accumulated_research_next,
        "all_company_standings": all_company_results["standings"],
        "all_company_net_assets_point": all_company_results["net_assets_point"],
        "market_report_subscriptions": realized_subscribed_markets,
    }
