from dataclasses import replace
from math import isclose

from exschool_game.engine import ExschoolSimulator
from exschool_game.export_report_html import render_report_html
from exschool_game.app import _equation_rows
from exschool_game.workforce import smoothed_average_salary


def test_round1_default_hr_and_finance_match_historical_report_shape() -> None:
    sim = ExschoolSimulator()
    payload = sim.stateful_default_payload("r1", None)
    context = sim._context_with_campaign_state("r1", None)
    for market in payload["markets"].values():
        market["subscribed_market_report"] = False
    for market in ("Shanghai", "Chengdu", "Wuhan"):
        payload["markets"][market]["agent_change"] = 1
        payload["markets"][market]["subscribed_market_report"] = True

    decision = sim._build_simulation_input("r1", payload, context=context, headcount_is_delta=True)
    report = sim.simulate(decision)

    workers = next(row for row in report["hr_detail"] if row["category"] == "Workers")
    engineers = next(row for row in report["hr_detail"] if row["category"] == "Engineers")
    assert workers["working"] == 879
    assert workers["previous"] == 0
    assert workers["laid_off"] == 0
    assert workers["quits"] == 0
    assert workers["promoted_this_round"] == 0
    assert workers["promotion_ready"] == 0
    assert workers["avg"] > 0
    assert engineers["working"] == 335
    assert engineers["previous"] == 0
    assert engineers["laid_off"] == 0
    assert engineers["quits"] == 0
    assert engineers["promoted_this_round"] == 0
    assert engineers["promotion_ready"] == 0
    assert engineers["avg"] > 0

    finance_by_label = {label: (cash_flow, cash, debt_change, debt) for label, cash_flow, cash, debt_change, debt in report["finance_rows"]}
    assert finance_by_label["市场报告费用"][0] <= 0.0
    assert finance_by_label["质量投入"][0] <= 0.0

    storage_by_item = {row["item"]: row for row in report["storage_summary"]}
    assert storage_by_item["Components"]["unit_price"] > 0
    assert storage_by_item["Components"]["storage_cost"] >= 0
    assert storage_by_item["Products"]["unit_price"] > 0
    assert storage_by_item["Products"]["storage_cost"] >= 0

    html = render_report_html({"report": report, "company_name": "C13", "key_data": sim.key_data})
    assert f"¥{round(workers['avg']):,}" in html
    assert f"¥{round(engineers['avg']):,}" in html
    assert "¥nan" not in html


def test_promotion_timing_matches_two_round_rule() -> None:
    sim = ExschoolSimulator()

    r1_payload = sim.stateful_default_payload("r1", None)
    for market in r1_payload["markets"].values():
        market["agent_change"] = 0
        market["marketing_investment"] = 0
        market["price"] = max(float(market["price"]), 7600)
        market["subscribed_market_report"] = False
    r1_payload["markets"]["Shanghai"]["agent_change"] = 1
    r1_payload["workers"] = 100
    r1_payload["engineers"] = 100
    r1_payload["worker_salary"] = 2503
    r1_payload["engineer_salary"] = 4709
    r1_payload["management_investment"] = 0
    r1_payload["quality_investment"] = 0
    r1_payload["research_investment"] = 0
    r1_payload["products_planned"] = 0
    r1_context = sim._context_with_campaign_state("r1", None)
    r1_decision = sim._build_simulation_input("r1", r1_payload, context=r1_context, headcount_is_delta=True)
    r1 = sim._simulate_with_context(r1_decision, r1_context, mode="campaign")
    state = sim._next_campaign_state(r1, r1_decision, None)

    r2_payload = sim.stateful_default_payload("r2", state)
    for market in r2_payload["markets"].values():
        market["agent_change"] = 0
        market["marketing_investment"] = 0
        market["price"] = max(float(market["price"]), 7600)
        market["subscribed_market_report"] = False
    r2_payload["workers"] = 0
    r2_payload["engineers"] = 0
    r2_payload["worker_salary"] = 2503
    r2_payload["engineer_salary"] = 4709
    r2_payload["management_investment"] = 0
    r2_payload["quality_investment"] = 0
    r2_payload["research_investment"] = 0
    r2_payload["products_planned"] = 0
    r2_context = sim._context_with_campaign_state("r2", state)
    r2_decision = sim._build_simulation_input("r2", r2_payload, context=r2_context, headcount_is_delta=True)
    r2 = sim._simulate_with_context(r2_decision, r2_context, mode="campaign")
    state = sim._next_campaign_state(r2, r2_decision, state)

    r2_workers = next(row for row in r2["hr_detail"] if row["category"] == "Workers")
    r2_engineers = next(row for row in r2["hr_detail"] if row["category"] == "Engineers")
    assert r2_workers["promoted_this_round"] == 0
    assert r2_workers["promotion_ready"] == r2_workers["working"]
    assert r2_engineers["promoted_this_round"] == 0
    assert r2_engineers["promotion_ready"] == r2_engineers["working"]

    r3_payload = sim.stateful_default_payload("r3", state)
    for market in r3_payload["markets"].values():
        market["agent_change"] = 0
        market["marketing_investment"] = 0
        market["price"] = max(float(market["price"]), 7600)
        market["subscribed_market_report"] = False
    r3_payload["workers"] = 0
    r3_payload["engineers"] = 0
    r3_payload["worker_salary"] = 2503
    r3_payload["engineer_salary"] = 4709
    r3_payload["management_investment"] = 0
    r3_payload["quality_investment"] = 0
    r3_payload["research_investment"] = 0
    r3_payload["products_planned"] = 0
    r3_context = sim._context_with_campaign_state("r3", state)
    r3 = sim._simulate_with_context(sim._build_simulation_input("r3", r3_payload, context=r3_context, headcount_is_delta=True), r3_context, mode="campaign")

    r3_workers = next(row for row in r3["hr_detail"] if row["category"] == "Workers")
    r3_engineers = next(row for row in r3["hr_detail"] if row["category"] == "Engineers")
    assert r3_workers["promoted_this_round"] == (
        r2_workers["promotion_ready"]
        - r3_workers["laid_off"]
        - r3_workers["quits"]
    )
    assert r3_workers["previous_experienced"] == 0
    assert r3_workers["experienced"] == r3_workers["promoted_this_round"]
    assert r3_workers["working"] == 0
    assert 0 <= r3_engineers["promoted_this_round"] <= r2_engineers["promotion_ready"]
    assert r3_engineers["previous_experienced"] == 0
    assert r3_engineers["experienced"] == r3_engineers["promoted_this_round"]
    assert r3_engineers["working"] == 0

    r3_html = render_report_html({"report": r3, "company_name": "C13", "key_data": sim.key_data})
    assert f">{-r3_workers['promoted_this_round']}<" in r3_html
    assert f">{r2_workers['promotion_ready']} workers are ready to be promoted in the next round.<" not in r3_html


def test_equations_rows_are_structured_without_header_noise() -> None:
    sim = ExschoolSimulator()
    rows = _equation_rows(sim.key_data)
    assert rows
    assert rows[0]["item"] == "1 Component"
    assert rows[0]["formula"] == "3 Inexperienced Workers + 7 Hours + 1 Component Material"
    assert all(row["item"] not in {"Equations & Ranges & Prices", "Item"} for row in rows)


def test_smoothed_average_salary_uses_two_fifths_step() -> None:
    assert smoothed_average_salary(4_000.0, 3_000.0) == 3_400.0
    assert smoothed_average_salary(2_000.0, 3_000.0) == 2_600.0


def test_single_player_average_salary_anchor_branch_applies_two_fifths_smoothing() -> None:
    sim = ExschoolSimulator()
    context = sim._context_with_campaign_state("r1", None)
    payload = sim.stateful_default_payload("r1", None)
    payload["workers"] = 100
    payload["engineers"] = 40
    payload["worker_salary"] = 6_000
    payload["engineer_salary"] = 12_000

    decision = sim._build_simulation_input("r1", payload, context=context, headcount_is_delta=True)
    previous_worker_avg = 3_000.0
    previous_engineer_avg = 6_000.0
    anchor = sim.round_salary_anchors["r1"]
    current_worker_avg = (
        float(anchor["peer_worker_payroll"]) + float(decision.workers) * float(decision.worker_salary)
    ) / (float(anchor["peer_workers"]) + float(decision.workers))
    current_engineer_avg = (
        float(anchor["peer_engineer_payroll"]) + float(decision.engineers) * float(decision.engineer_salary)
    ) / (float(anchor["peer_engineers"]) + float(decision.engineers))

    worker_avg, engineer_avg = sim._current_global_average_salary(
        "r1",
        decision,
        previous_worker_avg,
        previous_engineer_avg,
    )

    assert isclose(worker_avg, smoothed_average_salary(current_worker_avg, previous_worker_avg))
    assert isclose(engineer_avg, smoothed_average_salary(current_engineer_avg, previous_engineer_avg))


def test_round2_default_payload_uses_round2_decision_defaults_not_round1_workforce() -> None:
    sim = ExschoolSimulator()
    payload_r1 = sim.stateful_default_payload("r1", None)
    context_r1 = sim._context_with_campaign_state("r1", None)
    for market in payload_r1["markets"].values():
        market["subscribed_market_report"] = False
    payload_r1["markets"]["Shanghai"]["agent_change"] = 1
    payload_r1["worker_salary"] = 3333
    payload_r1["engineer_salary"] = 6666
    decision_r1 = sim._build_simulation_input("r1", payload_r1, context=context_r1, headcount_is_delta=True)
    report_r1 = sim._simulate_with_context(decision_r1, context_r1, mode="campaign")
    state = sim._next_campaign_state(report_r1, decision_r1, None)

    payload_r2 = sim.stateful_default_payload("r2", state)
    assert payload_r2["workers"] == sim.round_contexts["r2"]["workers_actual"] - state.workers
    assert payload_r2["engineers"] == sim.round_contexts["r2"]["engineers_actual"] - state.engineers
    assert payload_r2["worker_salary"] == 3333
    assert payload_r2["engineer_salary"] == 6666
    assert payload_r2["products_planned"] == sim.round_contexts["r2"]["products_produced_actual"]


def test_inventory_and_research_carry_across_rounds() -> None:
    sim = ExschoolSimulator()
    ctx1 = sim._context_with_campaign_state("r1", None)
    payload1 = sim.stateful_default_payload("r1", None)
    for market in payload1["markets"].values():
        market["agent_change"] = 0
        market["marketing_investment"] = 0
        market["price"] = 25_000
        market["subscribed_market_report"] = False
    payload1["markets"]["Shanghai"]["agent_change"] = 1
    payload1["workers"] = 300
    payload1["engineers"] = 10
    payload1["worker_salary"] = 2503
    payload1["engineer_salary"] = 4709
    payload1["management_investment"] = 0
    payload1["quality_investment"] = 1_000
    payload1["research_investment"] = 6_000_000
    payload1["products_planned"] = 1_000
    d1 = sim._build_simulation_input("r1", payload1, context=ctx1, headcount_is_delta=True)
    r1 = sim._simulate_with_context(d1, ctx1, mode="campaign")
    state = sim._next_campaign_state(r1, d1, None)

    assert state.active_patents in {0, 1}
    if state.active_patents == 1:
        assert state.accumulated_research_investment == 0
    else:
        assert state.accumulated_research_investment == 6_000_000

    carry_state = replace(state, component_inventory=321.0)

    ctx2 = sim._context_with_campaign_state("r2", carry_state)
    payload2 = sim.stateful_default_payload("r2", carry_state)
    for market in payload2["markets"].values():
        market["agent_change"] = 0
        market["marketing_investment"] = 0
        market["price"] = 25_000
        market["subscribed_market_report"] = False
    payload2["markets"]["Shanghai"]["agent_change"] = 0
    payload2["workers"] = 0
    payload2["engineers"] = 0
    payload2["worker_salary"] = 2503
    payload2["engineer_salary"] = 4709
    payload2["management_investment"] = 0
    payload2["quality_investment"] = 1_000
    payload2["research_investment"] = 0
    payload2["products_planned"] = 0
    d2 = sim._build_simulation_input("r2", payload2, context=ctx2, headcount_is_delta=True)
    r2 = sim._simulate_with_context(d2, ctx2, mode="campaign")

    components_row = next(row for row in r2["production_overview"] if row["item"] == "Components")
    assert components_row["previous"] == carry_state.component_inventory
    assert components_row["total"] == carry_state.component_inventory
    assert r2["research_summary"]["previous"] == carry_state.active_patents
