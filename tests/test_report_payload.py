import pandas as pd

from exschool_game.models import MarketDecision, SimulationInput
from exschool_game.report_payload import build_market_results, build_peer_market_tables, build_report_notes
from exschool_game.engine import ExschoolSimulator
from exschool_game.export_report_html import render_report_html


def test_build_market_results_marks_subscriptions_and_preserves_values() -> None:
    team_market_df = pd.DataFrame(
        [
            {
                "market": "Shanghai",
                "predicted_theoretical_cpi": 0.3,
                "simulated_sales_volume": 100.0,
                "simulated_marketshare": 0.1,
                "price": 20_000.0,
                "simulated_sales_revenue": 2_000_000.0,
                "agents_before": 1,
                "agent_change": 2,
                "agents_after": 3,
                "marketing_investment": 5_000.0,
            }
        ]
    )
    decision = SimulationInput(
        round_id="r1",
        loan_delta=0.0,
        workers=0,
        engineers=0,
        worker_salary=0.0,
        engineer_salary=0.0,
        management_investment=0.0,
        quality_investment=0.0,
        research_investment=0.0,
        products_planned=0,
        market_decisions={"Shanghai": MarketDecision(2, 5_000.0, 20_000.0, True)},
    )
    results = build_market_results(
        team_market_df=team_market_df,
        decision=decision,
        market_defaults={"Shanghai": {"previous_agents": 1}},
        subscribed_markets=["Shanghai"],
    )
    assert results[0]["market"] == "Shanghai"
    assert results[0]["subscribed_market_report"] is True
    assert results[0]["sales_revenue"] == 2_000_000.0


def test_build_peer_market_tables_sorts_and_normalizes_rows() -> None:
    full_market_df = pd.DataFrame(
        [
            {
                "market": "Shanghai",
                "active_market": True,
                "final_sales": 50.0,
                "market_size": 100.0,
                "team": 13,
                "management_index": 1.0,
                "agents": 2,
                "marketing_investment": 1_000.0,
                "quality_index": 1.5,
                "price": 20_000.0,
                "predicted_theoretical_cpi": 0.3,
                "predicted_marketshare_unconstrained": 0.4,
            },
            {
                "market": "Shanghai",
                "active_market": True,
                "final_sales": 40.0,
                "market_size": 100.0,
                "team": 7,
                "management_index": 0.8,
                "agents": 1,
                "marketing_investment": 500.0,
                "quality_index": 1.0,
                "price": 21_000.0,
                "predicted_theoretical_cpi": 0.2,
                "predicted_marketshare_unconstrained": 0.2,
            },
        ]
    )
    tables = build_peer_market_tables(full_market_df, ["Shanghai"])
    assert list(tables) == ["Shanghai"]
    assert tables["Shanghai"][0]["team"] == "13"
    assert "final_sales" not in tables["Shanghai"][0]
    assert tables["Shanghai"][0]["sales_volume_exact"] == 50.0


def test_build_report_notes_switches_campaign_copy() -> None:
    single_notes = build_report_notes("single")
    campaign_notes = build_report_notes("campaign")
    assert "多轮模式会延续现金" not in single_notes[2]
    assert "多轮模式会延续现金" in campaign_notes[2]


def test_rendered_report_still_contains_core_sections_after_payload_extraction() -> None:
    sim = ExschoolSimulator()
    payload = sim.stateful_default_payload("r1", None)
    context = sim._context_with_campaign_state("r1", None)
    for market in payload["markets"].values():
        market["subscribed_market_report"] = False
    payload["workers"] = 10
    payload["engineers"] = 5
    payload["worker_salary"] = 2_500.0
    payload["engineer_salary"] = 4_700.0
    payload["products_planned"] = 10
    payload["markets"]["Shanghai"]["agent_change"] = 1
    payload["markets"]["Shanghai"]["price"] = 20_000.0
    payload["markets"]["Shanghai"]["subscribed_market_report"] = True
    decision = sim._build_simulation_input("r1", payload, context=context, headcount_is_delta=True)
    report = sim.simulate(decision)
    html = render_report_html({"report": report, "company_name": "C13", "key_data": sim.key_data})
    assert "Human Resources" in html
    assert "Research Investment" in html
    assert "Market Report - Shanghai" in html
    assert "class='highlight-row'><td>13</td>" in html or 'class="highlight-row"><td>13</td>' in html
    assert "BSDAN" in html
    assert "ASDAN" not in html
    assert "Team Number:</span> <span class=\"value\">13</span>" in html or "Team Number:</span> <span class='value'>13</span>" in html
    assert "watermark" not in html


def test_validation_allows_zero_price_for_market_without_agents() -> None:
    sim = ExschoolSimulator()
    context = sim._context_with_campaign_state("r1", None)
    decision = SimulationInput(
        round_id="r1",
        loan_delta=0.0,
        workers=10,
        engineers=10,
        worker_salary=2500.0,
        engineer_salary=4700.0,
        management_investment=0.0,
        quality_investment=0.0,
        research_investment=0.0,
        products_planned=5,
        market_decisions={
            "Shanghai": MarketDecision(1, 0.0, 10_000.0, False),
            "Chengdu": MarketDecision(0, 0.0, 0.0, False),
            "Wuhan": MarketDecision(0, 0.0, 0.0, False),
            "Wuxi": MarketDecision(0, 0.0, 0.0, False),
            "Ningbo": MarketDecision(0, 0.0, 0.0, False),
        },
    )
    errors = sim._validate(decision, context)
    assert not any("售价必须" in error for error in errors)


def test_report_exposes_salary_adjusted_capacity_details() -> None:
    sim = ExschoolSimulator()
    decision = SimulationInput(
        round_id="r1",
        loan_delta=0.0,
        workers=1,
        engineers=100,
        worker_salary=1_000.0,
        engineer_salary=5_000.0,
        management_investment=0.0,
        quality_investment=0.0,
        research_investment=0.0,
        products_planned=20,
        market_decisions={
            "Shanghai": MarketDecision(1, 0.0, 10_000.0, True),
            "Chengdu": MarketDecision(0, 0.0, 0.0, False),
            "Wuhan": MarketDecision(0, 0.0, 0.0, False),
            "Wuxi": MarketDecision(0, 0.0, 0.0, False),
            "Ningbo": MarketDecision(0, 0.0, 0.0, False),
        },
    )
    report = sim.simulate(decision)
    components_row = next(row for row in report["production_details"] if row["item"] == "Components")
    products_row = next(row for row in report["production_details"] if row["item"] == "Products")
    assert components_row["salary_ratio"] < 1.0
    assert components_row["productivity_multiplier"] < 1.0
    assert components_row["theoretical_capacity"] == report["production_summary"]["零件最大产能"]
    assert products_row["theoretical_capacity"] == report["production_summary"]["成品最大产能"]
    assert report["production_overview"][0]["produced"] <= components_row["theoretical_capacity"]
