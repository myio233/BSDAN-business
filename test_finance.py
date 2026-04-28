from exschool_game.finance import build_finance_rows, loan_limit_for_state, market_report_cost_from_decision


def test_market_report_cost_from_decision_scales_by_subscription_count() -> None:
    assert market_report_cost_from_decision(3, 200_000.0) == 600_000.0


def test_loan_limit_for_state_steps_up_with_net_assets() -> None:
    thresholds = (50_000_000.0, 150_000_000.0, 400_000_000.0)
    assert loan_limit_for_state(initial_limit=5_000_000.0, starting_cash=10_000_000.0, starting_debt=0.0, stage_thresholds=thresholds) == 5_000_000.0
    assert loan_limit_for_state(initial_limit=5_000_000.0, starting_cash=60_000_000.0, starting_debt=0.0, stage_thresholds=thresholds) == 6_000_000.0
    assert loan_limit_for_state(initial_limit=5_000_000.0, starting_cash=200_000_000.0, starting_debt=0.0, stage_thresholds=thresholds) == 8_000_000.0
    assert loan_limit_for_state(initial_limit=5_000_000.0, starting_cash=500_000_000.0, starting_debt=0.0, stage_thresholds=thresholds) == 10_000_000.0


def test_build_finance_rows_preserves_order_and_caps_tax_by_available_cash() -> None:
    result = build_finance_rows(
        starting_cash=100.0,
        starting_debt=0.0,
        loan_delta=0.0,
        principal_after=0.0,
        ordered_costs=[("工人工资支出", 80.0), ("工程师工资支出", 50.0)],
        revenue=20.0,
        market_report_cost=10.0,
        research_investment=30.0,
        interest=5.0,
        tax=100.0,
    )
    rows = result["finance_rows"]
    labels = [row[0] for row in rows]
    assert labels[:6] == ["本轮开始", "银行贷款 / 还款", "工人工资支出", "工程师工资支出", "销售收入", "市场报告费用"]
    assert labels[-3:] == ["税费扣减", "本轮结束（现金）", "期末总资产"] or labels[-4:] == ["税费扣减", "本轮结束（现金）", "期末总资产", "期末净资产"]
    tax_row = next(row for row in rows if row[0] == "税费扣减")
    assert tax_row[1] == -0.0
    assert result["ending_cash"] == 0.0
