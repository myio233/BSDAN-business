from __future__ import annotations

from typing import Sequence


def market_report_cost_from_decision(subscriptions: int, subscription_cost: float) -> float:
    return float(subscriptions) * float(subscription_cost)


def loan_limit_for_state(
    *,
    initial_limit: float,
    starting_cash: float,
    starting_debt: float,
    stage_thresholds: tuple[float, float, float],
) -> float:
    net_assets = float(starting_cash) - float(starting_debt)
    stage2_limit = min(max(initial_limit, 6_000_000.0), 10_000_000.0)
    stage3_limit = min(max(stage2_limit, 8_000_000.0), 10_000_000.0)
    stage4_limit = 10_000_000.0
    if net_assets < stage_thresholds[0]:
        return initial_limit
    if net_assets < stage_thresholds[1]:
        return stage2_limit
    if net_assets < stage_thresholds[2]:
        return stage3_limit
    return stage4_limit


def build_finance_rows(
    *,
    starting_cash: float,
    starting_debt: float,
    loan_delta: float,
    principal_after: float,
    ordered_costs: Sequence[tuple[str, float]],
    revenue: float,
    market_report_cost: float,
    research_investment: float,
    interest: float,
    tax: float,
) -> dict[str, object]:
    rows: list[tuple[str, float | None, float | None, float | None, float | None]] = [
        ("本轮开始", None, starting_cash, None, starting_debt),
        ("银行贷款 / 还款", loan_delta, starting_cash + loan_delta, loan_delta, principal_after),
    ]
    running_cash = max(float(starting_cash) + float(loan_delta), 0.0)
    running_debt = float(principal_after)

    def add_cost_row(label: str, amount: float) -> None:
        nonlocal running_cash
        paid = min(float(amount), running_cash)
        running_cash -= paid
        rows.append((label, -paid, running_cash, None, running_debt))

    for label, amount in ordered_costs:
        add_cost_row(label, amount)

    running_cash += float(revenue)
    rows.append(("销售收入", float(revenue), running_cash, None, running_debt))
    add_cost_row("市场报告费用", market_report_cost)
    add_cost_row("研发投入", research_investment)

    running_debt += float(interest)
    rows.append(("负债利息", None, running_cash, float(interest), running_debt))
    tax_paid = min(float(tax), running_cash)
    running_cash -= tax_paid
    rows.append(("税费扣减", -tax_paid, running_cash, None, running_debt))
    ending_cash = max(running_cash, 0.0)
    total_assets = ending_cash
    net_assets = total_assets - running_debt
    rows.append(("本轮结束（现金）", None, running_cash, None, running_debt))
    rows.append(("期末总资产", None, total_assets, None, None))
    rows.append(("期末净资产", None, net_assets, None, None))
    return {
        "finance_rows": rows,
        "ending_cash": ending_cash,
        "ending_debt": running_debt,
        "total_assets": total_assets,
        "net_assets": net_assets,
        "tax_paid": tax_paid,
    }
