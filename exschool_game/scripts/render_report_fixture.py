from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exschool_game.engine import get_simulator
from exschool_game.export_report_html import render_report_html


def parse_money(value) -> float:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0.0
    text = str(value).strip().replace("¥", "").replace(",", "").replace(" ", "")
    if text in {"", "--", "nan", "NaN"}:
        return 0.0
    if text.startswith("+"):
        text = text[1:]
    if text.startswith("-"):
        return -float(text[1:])
    return float(text)


def parse_money_or_none(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text in {"", "--", "nan", "NaN"}:
        return None
    return parse_money(value)


def build_from_reference_workbook(path: Path, payload: dict) -> dict:
    report = payload["report"]
    summary = pd.read_excel(path, sheet_name="Summary", header=None)
    finance = pd.read_excel(path, sheet_name="Finance")
    hr = pd.read_excel(path, sheet_name="HR Summary")
    production = pd.read_excel(path, sheet_name="Production")
    agents = pd.read_excel(path, sheet_name="Sales Agents")
    sales = pd.read_excel(path, sheet_name="Sales Result")

    summary_values = {}
    for idx in range(len(summary)):
        key = summary.iloc[idx, 0]
        value = summary.iloc[idx, 1]
        if pd.isna(key) or pd.isna(value):
            continue
        summary_values[str(key).strip()] = value

    report["total_assets"] = parse_money(summary_values.get("Total Assets"))
    report["ending_debt"] = parse_money(summary_values.get("Debt"))
    report["net_assets"] = parse_money(summary_values.get("Net Assets"))
    report["net_profit"] = parse_money(summary_values.get("Net Profit"))
    report["key_metrics"]["销售收入"] = parse_money(summary_values.get("Sales Revenue"))
    report["key_metrics"]["成本"] = parse_money(summary_values.get("Cost"))
    report["key_metrics"]["预计排名"] = int(summary_values.get("Rank", 0) or 0)

    finance_rows = []
    for _, row in finance.iterrows():
        finance_rows.append(
            (
                str(row["Items"]).strip(),
                parse_money_or_none(row.get("Cash Flow")),
                parse_money_or_none(row.get("Cash")),
                parse_money_or_none(row.get("Debt Change")),
                parse_money_or_none(row.get("Debt")),
            )
        )
    report["finance_rows"] = finance_rows
    report["starting_cash"] = parse_money_or_none(finance.iloc[0]["Cash"]) or 0.0
    report["ending_cash"] = parse_money_or_none(finance.iloc[len(finance) - 1]["Cash"]) or 0.0

    hr_map = {str(row["Category"]).strip(): row for _, row in hr.iterrows()}
    report["hr_detail"] = [
        {
            "category": "Workers",
            "previous": 0,
            "added": int(hr_map.get("Inexperienced Workers", {}).get("Working", 0) or 0),
            "laid_off": 0,
            "working": int(hr_map.get("Inexperienced Workers", {}).get("Working", 0) or 0),
            "salary": parse_money(hr_map.get("Inexperienced Workers", {}).get("Salary")),
            "avg": parse_money(hr_map.get("Inexperienced Workers", {}).get("Avg")),
        },
        {
            "category": "Engineers",
            "previous": 0,
            "added": int(hr_map.get("Inexperienced Engineers", {}).get("Working", 0) or 0),
            "laid_off": 0,
            "working": int(hr_map.get("Inexperienced Engineers", {}).get("Working", 0) or 0),
            "salary": parse_money(hr_map.get("Inexperienced Engineers", {}).get("Salary")),
            "avg": parse_money(hr_map.get("Inexperienced Engineers", {}).get("Avg")),
        },
    ]

    production_map = {str(row["Metric"]).strip(): row["Value"] for _, row in production.iterrows()}
    report["production_overview"] = [
        {
            "item": "Components",
            "plan": float(str(production_map["Components Plan"]).replace(",", "")),
            "produced": float(str(production_map["Components Produced"]).replace(",", "")),
            "used_sold": float(str(production_map["Components Produced"]).replace(",", "")),
            "surplus": 0.0,
        },
        {
            "item": "Products",
            "plan": float(str(production_map["Products Plan"]).replace(",", "")),
            "produced": float(str(production_map["Products Produced"]).replace(",", "")),
            "used_sold": float(str(production_map["Products Produced"]).replace(",", "")),
            "surplus": 0.0,
        },
    ]
    report["production_details"] = [
        {
            "item": "Components",
            "productivity": float(production_map["Components Productivity"]),
            "employees": int(hr_map.get("Inexperienced Workers", {}).get("Working", 0) or 0),
            "production": float(str(production_map["Components Produced"]).replace(",", "")),
            "material_price": parse_money(production_map["Components Material Price"]),
            "material_cost": parse_money(next(row[1] for row in finance_rows if row[0] == "Components material cost")),
        },
        {
            "item": "Products",
            "productivity": float(production_map["Products Productivity"]),
            "employees": int(hr_map.get("Inexperienced Engineers", {}).get("Working", 0) or 0),
            "production": float(str(production_map["Products Produced"]).replace(",", "")),
            "material_price": parse_money(production_map["Products Material Price"]),
            "material_cost": parse_money(next(row[1] for row in finance_rows if row[0] == "Products material cost")),
        },
    ]
    report["storage_summary"] = [
        {
            "item": "Components",
            "capacity_before": 0.0,
            "capacity_after": float(str(production_map["Components Produced"]).replace(",", "")),
            "increment": float(str(production_map["Components Produced"]).replace(",", "")),
            "unit_price": payload["key_data"]["markets"]["Shanghai"]["component_storage_unit_cost"],
            "storage_cost": abs(parse_money(next(row[1] for row in finance_rows if row[0] == "Components storage cost"))),
        },
        {
            "item": "Products",
            "capacity_before": 0.0,
            "capacity_after": float(str(production_map["Products Produced"]).replace(",", "")),
            "increment": float(str(production_map["Products Produced"]).replace(",", "")),
            "unit_price": 78.0,
            "storage_cost": abs(parse_money(next(row[1] for row in finance_rows if row[0] == "Products storage cost"))),
        },
    ]
    report["production_summary"]["质量投资"] = parse_money(production_map["Quality Investment"])
    report["production_summary"]["产品质量指数"] = float(production_map["Product Quality Index"])

    report["sales_agents_table"] = []
    for _, row in agents.iterrows():
        report["sales_agents_table"].append(
            {
                "market": str(row["Market"]).strip(),
                "previous": int(row["Previous"]),
                "change": int(str(row["Change"]).replace("+", "")),
                "after": int(row["After"]),
                "change_cost": parse_money(row["Change Cost"]),
                "marketing_investment": parse_money(row["Marketing Investment"]),
                "subscribed_market_report": str(row["Market"]).strip() in {"Shanghai", "Chengdu", "Wuhan"},
            }
        )

    report["market_results"] = []
    for _, row in sales.iterrows():
        report["market_results"].append(
            {
                "market": str(row["Market"]).strip(),
                "competitive_power": float(str(row["Competitive Power"]).replace("%", "")) / 100.0,
                "sales_volume": float(str(row["Sales Volume"]).replace(",", "")),
                "market_share": float(str(row["Market Share"]).replace("%", "")) / 100.0,
                "price": parse_money(row["Price"]),
                "sales_revenue": parse_money(row["Sales"]),
            }
        )

    report["title"] = "North-3.30-31SR Tianjin Yinghua Experimental School"

    market_report_path = path.parent / "report4_market_reports.xlsx"
    if market_report_path.exists():
        peer_market_tables = {}
        market_report_summaries = {}
        for sheet in ("Shanghai", "Chengdu", "Wuhan"):
            df = pd.read_excel(market_report_path, sheet_name=sheet, header=None)
            market_report_summaries[sheet] = {
                "population": str(df.iloc[2, 0]).strip(),
                "penetration": str(df.iloc[2, 1]).strip(),
                "market_size": str(df.iloc[2, 2]).strip(),
                "total_sales_volume": str(df.iloc[2, 3]).strip(),
                "avg_price": str(df.iloc[2, 4]).strip(),
            }
            rows = []
            for idx in range(5, len(df)):
                team = df.iloc[idx, 0]
                if pd.isna(team):
                    continue
                rows.append(
                    {
                        "team": str(team).strip(),
                        "management_index": parse_money(df.iloc[idx, 1]),
                        "agents": float(str(df.iloc[idx, 2]).replace(",", "")),
                        "marketing_investment": parse_money(df.iloc[idx, 3]),
                        "quality_index": float(str(df.iloc[idx, 4]).replace(",", "")),
                        "price": parse_money(df.iloc[idx, 5]),
                        "sales_volume_display": str(df.iloc[idx, 6]).strip(),
                        "sales_volume_exact": float(str(df.iloc[idx, 6]).replace(",", "")),
                        "display_marketshare": float(str(df.iloc[idx, 7]).replace("%", "")) / 100.0,
                    }
                )
            peer_market_tables[sheet] = rows
        report["peer_market_tables"] = peer_market_tables
        report["market_report_summaries"] = market_report_summaries
    return payload


def build_report(round_id: str, home_city: str, company_name: str) -> dict:
    simulator = get_simulator()
    ctx = simulator._context_with_campaign_state(round_id, None)
    ctx["current_home_city"] = home_city
    ctx["loan_limit"] = simulator._loan_limit_for_state(
        float(ctx.get("starting_cash", 0.0) or 0.0),
        float(ctx.get("starting_debt", 0.0) or 0.0),
        home_city,
    )
    payload = simulator._payload_for_context(round_id, ctx)
    subscribed_markets = {"Shanghai", "Chengdu", "Wuhan"}
    for market, row in payload["markets"].items():
        row["subscribed_market_report"] = market in subscribed_markets
    for market in ("Shanghai", "Chengdu", "Wuhan"):
        if market in payload["markets"]:
            payload["markets"][market]["agent_change"] = 1

    form = {"round_id": round_id}
    for key, value in payload.items():
        if key == "round_id":
            continue
        if key == "markets":
            for market, row in value.items():
                slug = market.lower()
                if row.get("subscribed_market_report", True):
                    form[f"{slug}_market_report"] = "1"
                form[f"{slug}_agent_change"] = str(row["agent_change"])
                form[f"{slug}_marketing_investment"] = str(row["marketing_investment"])
                form[f"{slug}_price"] = str(row["price"])
            continue
        form[key] = str(value)

    decision = simulator.parse_form(form)
    report = simulator._simulate_with_context(decision, ctx, mode="campaign")
    return {
        "company_name": company_name,
        "home_city": home_city,
        "home_city_label": home_city,
        "key_data": simulator.key_data,
        "report": report,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--round", default="r1")
    parser.add_argument("--home-city", default="Shanghai")
    parser.add_argument("--company-name", default="C13")
    parser.add_argument("--html-output", required=True)
    parser.add_argument("--json-output")
    parser.add_argument("--reference-workbook")
    args = parser.parse_args()

    payload = build_report(args.round, args.home_city, args.company_name)
    if args.reference_workbook:
        payload = build_from_reference_workbook(Path(args.reference_workbook), payload)
    html_output = Path(args.html_output)
    html_output.write_text(render_report_html(payload), encoding="utf-8")
    if args.json_output:
        Path(args.json_output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
