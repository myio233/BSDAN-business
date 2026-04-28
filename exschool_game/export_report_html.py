from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path


ARTBOARD_WIDTH = 794
ROUND_HEIGHTS = {
    "r1": 3920,
    "r2": 4270,
    "r3": 5428,
    "r4": 5528,
}
MARKET_ORDER = ["Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"]
PAGE_SCALES = {
    "r1": 1.0,
    "r2": 1.0,
    "r3": 1.0,
    "r4": 1.0,
}
PAGE_Y_SCALES = {
    "r1": 1.17,
    "r2": 1.0,
    "r3": 1.0,
    "r4": 1.0,
}

FINANCE_LABELS = {
    "本轮开始": "Round begins",
    "银行贷款 / 还款": "Bank loan / repayment",
    "工人工资支出": "Workers salary cost",
    "工人工资": "Workers salary cost",
    "工程师工资支出": "Engineers salary cost",
    "工程师工资": "Engineers salary cost",
    "零件材料成本": "Components material cost",
    "零部件材料成本": "Components material cost",
    "零件仓储成本": "Components storage cost",
    "零部件仓储成本": "Components storage cost",
    "成品材料成本": "Products material cost",
    "成品仓储成本": "Products storage cost",
    "裁员补偿": "Layoff cost",
    "降薪离职补偿": "Salary reduction penalty",
    "销售代理调整成本": "Change sales agents cost",
    "代理调整成本": "Change sales agents cost",
    "营销投入": "Marketing investment",
    "市场投资": "Marketing investment",
    "质量投入": "Quality investment",
    "质量投资": "Quality investment",
    "管理投入": "Management Investment",
    "管理投资": "Management Investment",
    "销售收入": "Sales revenue",
    "市场报告费用": "Market report cost",
    "研发投入": "Research investment",
    "负债利息": "Debt interest",
    "利息支出": "Debt interest",
    "税费扣减": "Tax deduction",
    "所得税": "Tax deduction",
    "本轮结束（现金）": "Round ends",
    "本轮结束": "Round ends",
    "期末总资产": "Total assets",
    "期末净资产": "Net assets",
}


def money(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"¥{float(value):,.0f}"


def number(value: float | int | None, digits: int = 0) -> str:
    if value is None:
        return ""
    return f"{float(value):,.{digits}f}"


def pct(value: float | int | None) -> str:
    if value is None:
        return ""
    return f"{float(value) * 100:.2f}%"


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _summary_number(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    return number(float(value))


def _summary_money(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    return money(float(value))


def _summary_percent(value: object, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    return pct(float(value))


def _resolved_team_number(payload: dict, report: dict) -> str:
    for source in (payload, report):
        for key in ("team_number", "team_id", "team"):
            value = source.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text

    company_name = str(payload.get("company_name", "")).strip()
    match = re.fullmatch(r"[Cc]\s*(\d+)", company_name) or re.fullmatch(r"Team\s*(\d+)", company_name, re.IGNORECASE)
    if match:
        return match.group(1)

    return "13"


def load_payload(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _round_number(round_id: str) -> str:
    return "".join(ch for ch in round_id if ch.isdigit()) or round_id


def _logo_svg() -> str:
    return """
<svg class="logo-mark" viewBox="0 0 48 48" aria-hidden="true">
  <circle cx="24" cy="8" r="3.2" fill="#2db6d7"/>
  <circle cx="34.5" cy="12" r="3.2" fill="#5bc85b"/>
  <circle cx="40" cy="22" r="3.2" fill="#f6c54b"/>
  <circle cx="38" cy="33" r="3.2" fill="#ef7f43"/>
  <circle cx="29" cy="39.5" r="3.2" fill="#d64a8a"/>
  <circle cx="18.5" cy="39" r="3.2" fill="#7b5fd2"/>
  <circle cx="9.5" cy="31.5" r="3.2" fill="#4e7ee8"/>
  <circle cx="8" cy="20.5" r="3.2" fill="#2db6d7"/>
  <circle cx="14" cy="11.5" r="3.2" fill="#5bc85b"/>
  <circle cx="24" cy="24" r="4.3" fill="#ffffff" stroke="#d7d7d7" stroke-width="1"/>
</svg>
"""


def _section_title(title: str) -> str:
    return (
        "<div class='section-title-wrap'>"
        f"<div class='section-title'>{esc(title)}</div>"
        "<div class='section-rule'></div>"
        "</div>"
    )


def _metric_rows(report: dict) -> str:
    rank = report.get("key_metrics", {}).get("预计排名")
    return (
        "<table class='metric-table'>"
        "<tbody>"
        "<tr>"
        f"<th>Total Assets</th><th>Debt</th><th>Net Assets</th><th>Rank</th>"
        "</tr>"
        "<tr>"
        f"<td>{esc(money(report.get('total_assets')))}</td>"
        f"<td>{esc(money(report.get('ending_debt')))} <span class='operator'>+</span></td>"
        f"<td>{esc(money(report.get('net_assets')))}</td>"
        f"<td>{esc(rank if rank is not None else '')}</td>"
        "</tr>"
        "<tr class='metric-spacer'><td colspan='4'></td></tr>"
        "<tr>"
        "<th>Sales Revenue</th><th>Cost</th><th>Net Profit</th><th></th>"
        "</tr>"
        "<tr>"
        f"<td>{esc(money(report.get('key_metrics', {}).get('销售收入')))} <span class='operator'>-</span></td>"
        f"<td>{esc(money(report.get('key_metrics', {}).get('成本')))} <span class='operator'>+</span></td>"
        f"<td>{esc(money(report.get('net_profit')))}</td>"
        "<td></td>"
        "</tr>"
        "</tbody>"
        "</table>"
    )


def _bullet(text: str, suffix: str = "") -> str:
    suffix_html = f" <span class='explain'>{esc(suffix)}</span>" if suffix else ""
    return f"<li><span class='dot'>•</span> {esc(text)}{suffix_html}</li>"


def _finance_table(report: dict) -> str:
    rows = []
    for label, cash_flow, cash, debt_change, debt in report.get("finance_rows", []):
        if label in {"研发投入", "期末总资产", "期末净资产"}:
            continue
        display_label = FINANCE_LABELS.get(str(label), str(label))
        rows.append(
            "<tr>"
            f"<td class='label-col'>{esc(display_label)}</td>"
            f"<td>{esc(money(cash_flow) if cash_flow is not None else '--')}</td>"
            f"<td>{esc(money(cash) if cash is not None else '--')}</td>"
            f"<td>{esc(money(debt_change) if debt_change is not None else '--')}</td>"
            f"<td>{esc(money(debt) if debt is not None else '--')}</td>"
            "</tr>"
        )
    return (
        "<table class='grid-table finance-table'>"
        "<thead><tr><th class='label-col'>Items</th><th>Cash Flow</th><th>Cash</th><th>Debt Change</th><th>Debt</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _hr_rows(report: dict) -> list[dict[str, object]]:
    workers = next((row for row in report.get("hr_detail", []) if row.get("category") == "Workers"), None)
    engineers = next((row for row in report.get("hr_detail", []) if row.get("category") == "Engineers"), None)
    return [
        {
            "label": "Inexperienced\nWorkers",
            "previous": workers.get("previous", 0) if workers else 0,
            "laid": workers.get("laid_off_inexperienced", workers.get("laid_off", 0)) if workers else 0,
            "quitted": workers.get("quits_inexperienced", workers.get("quits", 0)) if workers else 0,
            "added": workers.get("added", 0) if workers else 0,
            "promoted": -(workers.get("promoted_this_round", 0) if workers else 0),
            "working": workers.get("working", 0) if workers else 0,
            "salary": workers.get("salary", 0.0) if workers else 0.0,
            "avg": workers.get("avg", 0.0) if workers else 0.0,
            "salary_ratio": workers.get("salary_ratio", 1.0) if workers else 1.0,
            "productivity_multiplier": workers.get("productivity_multiplier", 1.0) if workers else 1.0,
        },
        {
            "label": "Experienced\nWorkers",
            "previous": workers.get("previous_experienced", 0) if workers else 0,
            "laid": workers.get("laid_off_experienced", 0) if workers else 0,
            "quitted": workers.get("quits_experienced", 0) if workers else 0,
            "added": 0,
            "promoted": workers.get("promoted_this_round", 0) if workers else 0,
            "working": workers.get("experienced", 0) if workers else 0,
            "salary": workers.get("salary", 0.0) if workers else 0.0,
            "avg": workers.get("avg", 0.0) if workers else 0.0,
            "salary_ratio": workers.get("salary_ratio", 1.0) if workers else 1.0,
            "productivity_multiplier": workers.get("productivity_multiplier", 1.0) if workers else 1.0,
        },
        {
            "label": "Inexperienced\nEngineers",
            "previous": engineers.get("previous", 0) if engineers else 0,
            "laid": engineers.get("laid_off_inexperienced", engineers.get("laid_off", 0)) if engineers else 0,
            "quitted": engineers.get("quits_inexperienced", engineers.get("quits", 0)) if engineers else 0,
            "added": engineers.get("added", 0) if engineers else 0,
            "promoted": -(engineers.get("promoted_this_round", 0) if engineers else 0),
            "working": engineers.get("working", 0) if engineers else 0,
            "salary": engineers.get("salary", 0.0) if engineers else 0.0,
            "avg": engineers.get("avg", 0.0) if engineers else 0.0,
            "salary_ratio": engineers.get("salary_ratio", 1.0) if engineers else 1.0,
            "productivity_multiplier": engineers.get("productivity_multiplier", 1.0) if engineers else 1.0,
        },
        {
            "label": "Experienced\nEngineers",
            "previous": engineers.get("previous_experienced", 0) if engineers else 0,
            "laid": engineers.get("laid_off_experienced", 0) if engineers else 0,
            "quitted": engineers.get("quits_experienced", 0) if engineers else 0,
            "added": 0,
            "promoted": engineers.get("promoted_this_round", 0) if engineers else 0,
            "working": engineers.get("experienced", 0) if engineers else 0,
            "salary": engineers.get("salary", 0.0) if engineers else 0.0,
            "avg": engineers.get("avg", 0.0) if engineers else 0.0,
            "salary_ratio": engineers.get("salary_ratio", 1.0) if engineers else 1.0,
            "productivity_multiplier": engineers.get("productivity_multiplier", 1.0) if engineers else 1.0,
        },
    ]


def _human_resources(report: dict) -> str:
    rows = []
    for row in _hr_rows(report):
        salary_ratio = row.get("salary_ratio", 1.0)
        productivity_multiplier = row.get("productivity_multiplier", 1.0)
        rows.append(
            "<tr>"
            f"<td class='label-col multiline'>{esc(row['label'])}</td>"
            f"<td>{esc(row['previous'])}</td>"
            f"<td>{esc(_signed_headcount(-int(row['laid'] or 0)))}</td>"
            f"<td>{esc(_signed_headcount(-int(row['quitted'] or 0)))}</td>"
            f"<td>{esc(_signed_headcount(row['added']))}</td>"
            f"<td>{esc(_signed_headcount(row['promoted']))}</td>"
            f"<td>{esc(row['working'])}</td>"
            f"<td>{esc(money(row['salary']))}</td>"
            f"<td>{esc(money(row['avg']))}</td>"
            f"<td>{esc(number(salary_ratio, 3))}</td>"
            f"<td>{esc(number(productivity_multiplier, 3))}</td>"
            "</tr>"
        )
    return (
        "<table class='grid-table'>"
        "<thead><tr><th class='label-col'>Employees</th><th>Previous</th><th>Laid</th><th>Quitted</th><th>Added</th><th>Promoted</th><th>Working</th><th>Salary</th><th>Avg.</th><th>Salary/Avg.</th><th>Capacity Mult.</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
    )


def _management_table(report: dict) -> str:
    mgmt = report.get("management_summary", {})
    return (
        "<table class='grid-table compact-table'>"
        "<thead><tr><th>Management</th><th>Planned Management Investment</th><th>Management Investment</th><th>Management Index</th></tr></thead>"
        "<tbody>"
        f"<tr><td></td><td>{esc(money(mgmt.get('planned_investment')))}</td><td>{esc(money(mgmt.get('investment')))}</td><td>{esc(number(mgmt.get('index'), 2))}</td></tr>"
        "</tbody></table>"
    )


def _promotion_note(report: dict, category: str) -> str:
    row = next((item for item in report.get("hr_detail", []) if item.get("category") == category), {})
    ready = int(row.get("promotion_ready", 0) or 0)
    label = "Worker Promotion" if category == "Workers" else "Engineer Promotion"
    noun = "workers" if category == "Workers" else "engineers"
    return _bullet(label, f"{ready} {noun} are ready to be promoted in the next round.")


def _signed_headcount(value: object) -> str:
    number_value = int(value or 0)
    return f"{number_value:+d}" if number_value > 0 else str(number_value)


def _production_tables(report: dict) -> str:
    overview_rows = []
    for row in report.get("production_overview", []):
        overview_rows.append(
            "<tr>"
            f"<td class='label-col'>{esc(row['item'])}</td>"
            f"<td>{esc(number(row['plan']))}</td>"
            f"<td>{esc(number(row.get('previous', 0.0)))}</td>"
            f"<td>{esc(number(row['produced']))}</td>"
            f"<td>{esc(number(row.get('total', row['produced'])))}</td>"
            f"<td>{esc(number(row['used_sold']))}</td>"
            f"<td>{esc(number(row['surplus']))}</td>"
            "</tr>"
        )
    detail_rows = []
    for row in report.get("production_details", []):
        detail_rows.append(
            "<tr>"
            f"<td class='label-col'>{esc(row['item'])}</td>"
            f"<td>{esc(number(row.get('base_productivity', row['productivity']), 3))}</td>"
            f"<td>{esc(money(row.get('salary')))}</td>"
            f"<td>{esc(money(row.get('benchmark_salary')))}</td>"
            f"<td>{esc(number(row.get('salary_ratio'), 3))}</td>"
            f"<td>{esc(number(row.get('productivity_multiplier'), 3))}</td>"
            f"<td>{esc(number(row['productivity'], 3))}</td>"
            f"<td>{esc(number(row['employees']))}</td>"
            f"<td>{esc(number(row.get('theoretical_capacity', 0)))}</td>"
            f"<td>{esc(number(row['production']))}</td>"
            f"<td>{esc(money(row['material_price']))}</td>"
            f"<td>{esc(money(row['material_cost']))}</td>"
            "</tr>"
        )
    return (
        "<table class='grid-table compact-table'>"
        "<thead><tr><th class='label-col'>Overview</th><th>Plan</th><th>Previous</th><th>Produced</th><th>Total</th><th>Used/Sold</th><th>Surplus</th></tr></thead>"
        f"<tbody>{''.join(overview_rows)}</tbody>"
        "</table>"
        "<div class='table-gap'></div>"
        "<table class='grid-table compact-table'>"
        "<thead><tr><th class='label-col'>Details</th><th>Base Productivity</th><th>Salary</th><th>Avg. Salary</th><th>Salary/Avg.</th><th>Capacity Mult.</th><th>Adjusted Productivity</th><th>Employees</th><th>Theoretical Output</th><th>Production</th><th>Material Price</th><th>Material Cost</th></tr></thead>"
        f"<tbody>{''.join(detail_rows)}</tbody>"
        "</table>"
    )


def _storage_quality_research(report: dict) -> str:
    storage_rows = []
    for row in report.get("storage_summary", []):
        storage_rows.append(
            "<tr>"
            f"<td class='label-col'>{esc(row['item'])}</td>"
            f"<td>{esc(number(row['capacity_before']))}</td>"
            f"<td>{esc(number(row['capacity_after']))}</td>"
            f"<td>{esc(number(row['increment']))}</td>"
            f"<td>{esc(money(row['unit_price']))}</td>"
            f"<td>{esc(money(row['storage_cost']))}</td>"
            "</tr>"
        )
    summary = report.get("production_summary", {})
    produced_products = float(summary.get("新成品", 0.0) or 0.0)
    old_products = float(summary.get("旧成品", 0.0) or 0.0)
    quality_index = summary.get("产品质量指数", 0.0)
    quality_investment = summary.get("质量投资", 0.0)
    research = report.get("research_summary", {})
    return (
        "<table class='grid-table compact-table'>"
        "<thead><tr><th class='label-col'>Storage</th><th>Capacity Before</th><th>Capacity After</th><th>Increment</th><th>Unit Price</th><th>Storage Cost</th></tr></thead>"
        f"<tbody>{''.join(storage_rows)}</tbody>"
        "</table>"
        "<ul class='notes'>"
        f"{_bullet('Storage Cost', 'You only need to spend money on increasing your storage capacity.')}"
        "</ul>"
        f"{_section_title('Quality')}"
        "<table class='grid-table compact-table'>"
        "<thead><tr><th class='label-col'>Quality</th><th>Quality Investment</th><th>Old Products</th><th>New Products</th><th>Product Quality Index</th></tr></thead>"
        "<tbody>"
        f"<tr><td></td><td>{esc(money(quality_investment))}</td><td>{esc(number(old_products))}</td><td>{esc(number(produced_products))}</td><td>{esc(number(quality_index, 2))}</td></tr>"
        "</tbody></table>"
        "<ul class='notes'>"
        f"{_bullet('Product Quality Index = Quality Investment ÷ (Old Products × 1.20 + New Products)')}"
        "</ul>"
        f"{_section_title('Research Investment')}"
        "<table class='grid-table compact-table'>"
        "<thead><tr><th class='label-col'>Overview</th><th>Previous</th><th>Change</th><th>After</th><th>Accumulated Research Investment</th></tr></thead>"
        "<tbody>"
        f"<tr><td>Patents</td><td>{esc(research.get('previous', 0))}</td><td>{esc(_signed_headcount(research.get('change', 0)))}</td><td>{esc(research.get('after', research.get('patents', 0)))}</td><td>{esc(money(research.get('accumulated')))}</td></tr>"
        "</tbody></table>"
        + "<ul class='notes'>"
        + _bullet("Research Probability", pct(research.get("probability", 0.0)) + " chance from this round's accumulated research pool.")
        + _bullet("Accumulated Research Investment", "If your research is not successful, your research investment is accumulated to the next round.")
        + "</ul>"
    )


def _sales_tables(report: dict) -> str:
    agent_rows = []
    for row in report.get("sales_agents_table", []):
        agent_rows.append(
            "<tr>"
            f"<td class='label-col'>{esc(row['market'])}</td>"
            f"<td>{esc(row['previous'])}</td>"
            f"<td>{esc(format(int(row['change']), '+d'))}</td>"
            f"<td>{esc(row['after'])}</td>"
            f"<td>{esc(money(row['change_cost']))}</td>"
            f"<td>{esc(money(row['marketing_investment']))}</td>"
            "</tr>"
        )
    market_rows = []
    for row in report.get("market_results", []):
        market_rows.append(
            "<tr>"
            f"<td class='label-col'>{esc(row['market'])}</td>"
            f"<td>{esc(pct(row['competitive_power']))}</td>"
            f"<td>{esc(number(row['sales_volume']))}</td>"
            f"<td>{esc(pct(row['market_share']))}</td>"
            f"<td>{esc(money(row['price']))}</td>"
            f"<td>{esc(money(row['sales_revenue']))}</td>"
            "</tr>"
        )
    return (
        "<table class='grid-table compact-table'>"
        "<thead><tr><th class='label-col'>Agents</th><th>Previous</th><th>Change</th><th>After</th><th>Change Cost</th><th>Marketing Investment</th></tr></thead>"
        f"<tbody>{''.join(agent_rows)}</tbody>"
        "</table>"
        "<div class='table-gap'></div>"
        "<table class='grid-table compact-table'>"
        "<thead><tr><th class='label-col'>Market</th><th>Competitive Power</th><th>Sales Volume</th><th>Market Share</th><th>Price</th><th>Sales</th></tr></thead>"
        f"<tbody>{''.join(market_rows)}</tbody>"
        "</table>"
    )


def _market_summaries(report: dict, key_data: dict[str, object], highlight_team: str = "13") -> str:
    subscribed = set(report.get("market_report_subscriptions", []))
    peer_tables = report.get("peer_market_tables", {})
    report_summaries = report.get("market_report_summaries", {})
    sections = []
    ordered_markets = [market for market in MARKET_ORDER if market in peer_tables]
    ordered_markets.extend(market for market in peer_tables.keys() if market not in ordered_markets)
    for market in ordered_markets:
        if subscribed and market not in subscribed:
            continue
        market_cfg = dict(key_data.get("markets", {}).get(market, {}))
        rows = list(peer_tables.get(market, []))
        summary_row = dict(report_summaries.get(market, {}))
        population = _summary_number(
            summary_row.get("population"),
            number(float(market_cfg.get("population", 0.0) or 0.0)),
        )
        penetration = _summary_percent(
            summary_row.get("penetration"),
            f"{float(market_cfg.get('initial_penetration', 0.0) or 0.0) * 100:.2f}%",
        )
        market_size = _summary_number(
            summary_row.get("market_size"),
            number(float(market_cfg.get("population", 0.0) or 0.0) * float(market_cfg.get("initial_penetration", 0.0) or 0.0)),
        )
        total_sales_volume = summary_row.get("total_sales_volume")
        avg_price = summary_row.get("avg_price")
        if total_sales_volume is None or avg_price is None:
            market_size_value = float(market_cfg.get("population", 0.0) or 0.0) * float(market_cfg.get("initial_penetration", 0.0) or 0.0)
            total_sales_volume = number(sum(float(row.get("display_marketshare", 0.0) or 0.0) * market_size_value for row in rows))
            weighted_share = sum(float(row.get("display_marketshare", 0.0) or 0.0) for row in rows)
            avg_price = money(
                sum(float(row.get("price", 0.0) or 0.0) * float(row.get("display_marketshare", 0.0) or 0.0) for row in rows) / weighted_share
                if weighted_share > 0
                else 0.0
            )
        else:
            total_sales_volume = _summary_number(total_sales_volume, "")
            avg_price = _summary_money(avg_price, "")
        header = (
            f"{_section_title(f'Market Report - {market}')}"
            "<table class='grid-table compact-table market-summary-table'>"
            "<thead><tr><th>Population</th><th>Penetration</th><th>Market Size</th><th>Total Sales Volume</th><th>Avg. Price</th></tr></thead>"
            "<tbody>"
            f"<tr><td>{esc(population)}</td><td>{esc(penetration)}</td><td>{esc(market_size)}</td><td>{esc(total_sales_volume)}</td><td>{esc(avg_price)}</td></tr>"
            "</tbody></table>"
        )
        body_rows = []
        for row in rows:
            row_class = " class='highlight-row'" if str(row.get("team")) == str(highlight_team) else ""
            body_rows.append(
                f"<tr{row_class}>"
                f"<td>{esc(row.get('team'))}</td>"
                f"<td>{esc(number(row.get('management_index'), 2))}</td>"
                f"<td>{esc(number(row.get('agents')))}</td>"
                f"<td>{esc(money(row.get('marketing_investment')))}</td>"
                f"<td>{esc(number(row.get('quality_index'), 2))}</td>"
                f"<td>{esc(money(row.get('price')))}</td>"
                f"<td>{esc(number(float(row.get('display_sales_volume', row.get('sales_volume_exact', 0.0)) or 0.0)) if row.get('sales_volume_display') is None else row.get('sales_volume_display'))}</td>"
                f"<td>{esc(pct(row.get('display_marketshare')))}</td>"
                "</tr>"
            )
        if not body_rows:
            body_rows.append(
                "<tr>"
                "<td colspan='8' style='text-align:center;'>No active teams in this market</td>"
                "</tr>"
            )
        body = (
            "<table class='grid-table compact-table market-report-table'>"
            "<thead><tr><th>Team</th><th>Management Index</th><th>Agents</th><th>Marketing Investment</th><th>Product Quality Index</th><th>Price</th><th>Sales Volume</th><th>Market Share</th></tr></thead>"
            f"<tbody>{''.join(body_rows)}</tbody></table>"
        )
        sections.append(f"<section class='market-report-block'>{header}{body}</section>")
    return "".join(sections)


def render_report_html(payload: dict) -> str:
    report = payload["report"]
    company_name = payload.get("company_name", "")
    key_data = payload.get("key_data", {})
    team_number = _resolved_team_number(payload, report)
    round_id = str(report["round_id"]).lower()
    round_number = _round_number(round_id)
    artboard_height = ROUND_HEIGHTS.get(round_id, 4300)
    page_scale = PAGE_SCALES.get(round_id, 1.0)
    page_y_scale = PAGE_Y_SCALES.get(round_id, 1.0)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{esc(company_name)} {esc(report['round_id']).upper()} 财报</title>
  <style>
    :root {{
      --page-w: {ARTBOARD_WIDTH}px;
      --page-h: {artboard_height}px;
      --ink: #303030;
      --grid: #d7d7d7;
      --head: #9a9a9a;
      --paper: #ffffff;
      --bg: #f0f0f0;
      --scale: {page_scale};
      --y-scale: {page_y_scale};
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; padding: 0; background: var(--bg); color: var(--ink); }}
    body {{
      font-family: "Courier New", "Nimbus Mono PS", "Liberation Mono", monospace;
      -webkit-font-smoothing: antialiased;
      text-rendering: geometricPrecision;
    }}
    .download-bar {{
      width: var(--page-w);
      margin: 16px auto 12px;
      display: flex;
      justify-content: flex-end;
    }}
    .download-bar button {{
      border: 1px solid #bcbcbc;
      background: #fff;
      padding: 8px 12px;
      font: 12px/1.1 Arial, sans-serif;
      cursor: pointer;
    }}
    .sheet {{
      position: relative;
      width: var(--page-w);
      min-height: var(--page-h);
      margin: 0 auto 24px;
      background: var(--paper);
      box-shadow: 0 12px 30px rgba(0, 0, 0, 0.12);
      overflow: hidden;
    }}
    .capture-root {{
      position: relative;
      width: var(--page-w);
      background: var(--paper);
      overflow: hidden;
    }}
    .page-inner {{
      position: relative;
      padding: 14px 32px 40px;
      z-index: 1;
      transform: scaleY(var(--y-scale));
      transform-origin: top left;
      width: 100%;
      filter: contrast(0.95) saturate(0.96);
    }}
    .topbar {{
      display: grid;
      grid-template-columns: 168px 1fr 154px;
      align-items: end;
      column-gap: 8px;
    }}
    .brand {{
      display: flex;
      align-items: center;
      gap: 8px;
    }}
    .logo-mark {{ width: 34px; height: 34px; flex: none; }}
    .brand-main {{
      font-family: Arial, sans-serif;
      font-weight: 700;
      font-size: 20px;
      letter-spacing: .3px;
    }}
    .brand-sub {{
      font-family: Arial, sans-serif;
      font-size: 11px;
      color: #ba6170;
      margin-left: 44px;
      margin-top: -3px;
    }}
    .report-meta {{
      font-size: 11px;
      line-height: 1.18;
      padding-bottom: 4px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .team-meta {{
      text-align: right;
      font-family: Arial, sans-serif;
      padding-bottom: 4px;
    }}
    .team-meta .label {{ font-size: 10px; }}
    .team-meta .value {{ font-size: 23px; font-weight: 700; margin-left: 12px; }}
    .section-title-wrap {{ margin-top: 18px; }}
    .section-title {{
      font-size: 11px;
      font-weight: 700;
      margin-bottom: 5px;
    }}
    .section-rule {{
      width: 62%;
      border-top: 1px solid #4a4a4a;
      height: 0;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      font-size: 10px;
      line-height: 1.02;
    }}
    th, td {{
      border-bottom: 1px solid var(--grid);
      border-right: 1px solid var(--grid);
      padding: 3px 5px 2px;
      text-align: right;
      vertical-align: middle;
    }}
    th:last-child, td:last-child {{ border-right: none; }}
    thead th {{
      color: var(--head);
      font-weight: 700;
      text-align: center;
    }}
    .label-col {{
      text-align: left;
      width: 31%;
    }}
    .metric-table {{ margin-top: 6px; }}
    .metric-table th, .metric-table td {{
      font-size: 12px;
      padding: 5px 7px;
    }}
    .metric-table th {{
      color: var(--head);
      font-weight: 700;
      text-align: center;
    }}
    .metric-table td {{
      font-size: 14px;
      text-align: center;
    }}
    .metric-table .metric-spacer td {{
      border-bottom: none;
      height: 12px;
      padding: 0;
    }}
    .operator {{
      color: var(--ink);
      font-weight: 700;
      margin-left: 12px;
    }}
    .notes {{
      list-style: none;
      padding: 0;
      margin: 8px 0 0;
      font-size: 10px;
      line-height: 1.34;
    }}
    .notes li {{ margin: 0 0 5px; }}
    .dot {{ font-weight: 700; }}
    .explain {{
      font-style: italic;
      color: #666;
    }}
    .compact-table th, .compact-table td {{
      padding-top: 3px;
      padding-bottom: 2px;
    }}
    .multiline {{ white-space: pre-line; line-height: 1; }}
    .table-gap {{ height: 10px; }}
    .market-report-block {{ margin-top: 18px; }}
    .market-summary-table {{ margin-top: 6px; }}
    .market-summary-table th, .market-summary-table td {{ text-align: right; }}
    .market-summary-table thead th {{ text-align: center; }}
    @media print {{
      body {{ background: #fff; }}
      .download-bar {{ display: none; }}
      .sheet {{ margin: 0 auto; box-shadow: none; }}
    }}
  </style>
</head>
<body>
  <div class="download-bar"><button onclick="downloadImage()">下载图片</button></div>
  <article class="sheet">
    <div class="capture-root">
      <div class="page-inner">
      <header class="topbar">
        <div>
          <div class="brand">
            {_logo_svg()}
            <div>
              <div class="brand-main">BSDAN</div>
            </div>
          </div>
        </div>
        <div class="report-meta">
          <div>{esc(report.get('title', f'Round {round_number} Report'))}</div>
          <div>Round {esc(round_number)} Report</div>
        </div>
        <div class="team-meta"><span class="label">Team Number:</span> <span class="value">{esc(team_number)}</span></div>
      </header>

      {_section_title('Key Metrics')}
      {_metric_rows(report)}
      <ul class="notes">
        {_bullet('Net Profit = Sales Revenue - All Costs', 'The direct indicator of your achievement in this round.')}
        {_bullet('Net Assets = Total Assets - Debt', 'Your result till this round, used for ranking.')}
      </ul>

      {_section_title('Finance')}
      {_finance_table(report)}

      {_section_title('Human Resources')}
      {_human_resources(report)}
        <ul class="notes">
        {_bullet('Low-salary Effect', 'If your salary is relatively low, you cannot add as many employees as you planned to, and some employees may quit.')}
        {_bullet('Layoff Cost', "When you lay off your employees, you must compensate them for one month's salary.")}
        {_bullet('Salary-reduction Penalty', "When employees quit while you have reduction in salary, you must compensate them for two months' salary.")}
        {_promotion_note(report, 'Workers')}
        {_promotion_note(report, 'Engineers')}
        {_bullet('Compensations are based on the salary of previous round.')}
      </ul>

      <div style="margin-top:26px;">{_management_table(report)}</div>

      {_section_title('Production')}
      {_production_tables(report)}
      <ul class="notes">
        {_bullet('Productivity', 'Adjusted productivity = fixed round productivity × salary multiplier.')}
        {_bullet('Theoretical Output', 'Theoretical output = adjusted productivity × working employees.')}
        {_bullet('Production', 'Actual production is limited by your plan, worker/components capacity, engineer/products capacity and available components.')}
      </ul>

      <div style="margin-top:24px;">{_storage_quality_research(report)}</div>

      {_section_title('Sales')}
      {_sales_tables(report)}

      {_market_summaries(report, key_data, highlight_team=team_number)}
        <div class="report-end-marker" aria-hidden="true"></div>
      </div>
    </div>
  </article>
  <script>
    function downloadImage() {{
      const sheet = document.querySelector('.sheet');
      const styleText = Array.from(document.querySelectorAll('style')).map((node) => node.textContent || '').join('\\n');
      const pageW = {ARTBOARD_WIDTH};
      const pageH = {artboard_height};
      const payload = `
        <svg xmlns="http://www.w3.org/2000/svg" width="${{pageW}}" height="${{pageH}}">
          <foreignObject width="100%" height="100%">
            <div xmlns="http://www.w3.org/1999/xhtml">
              <style>html,body{{margin:0;padding:0;background:#fff;}} ${{styleText}}</style>
              ${{sheet.outerHTML}}
            </div>
          </foreignObject>
        </svg>`;
      const blob = new Blob([payload], {{ type: 'image/svg+xml;charset=utf-8' }});
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {{
        const canvas = document.createElement('canvas');
        canvas.width = pageW;
        canvas.height = pageH;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, pageW, pageH);
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);
        canvas.toBlob((pngBlob) => {{
          if (!pngBlob) return;
          const pngUrl = URL.createObjectURL(pngBlob);
          const a = document.createElement('a');
          a.href = pngUrl;
          a.download = {json.dumps(f"{company_name}-{report['round_id']}")} + '-' + Date.now() + '.png';
          a.click();
          setTimeout(() => URL.revokeObjectURL(pngUrl), 1000);
        }}, 'image/png');
      }};
      img.src = url;
    }}
  </script>
</body>
</html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a fixed-layout standalone HTML report from report JSON.")
    parser.add_argument("--input", required=True, help="Path to JSON payload with report/company metadata.")
    parser.add_argument("--output", required=True, help="Path to output HTML file.")
    args = parser.parse_args()
    payload = load_payload(Path(args.input))
    Path(args.output).write_text(render_report_html(payload), encoding="utf-8")


if __name__ == "__main__":
    main()
