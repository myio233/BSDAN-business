import pandas as pd

from exschool_game.engine import ExschoolSimulator
from exschool_game.export_report_html import _market_summaries
from exschool_game.models import MarketDecision, SimulationInput
from exschool_game.report_payload import build_market_report_summaries, build_peer_market_tables


MARKETS = ["Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"]


def _market_decisions(*, subscribed_shanghai: bool = True) -> dict[str, MarketDecision]:
    return {
        market: MarketDecision(
            agent_change=1 if market == "Shanghai" else 0,
            marketing_investment=0.0,
            price=10_000.0 if market == "Shanghai" else 0.0,
            subscribed_market_report=subscribed_shanghai if market == "Shanghai" else False,
        )
        for market in MARKETS
    }


def test_financial_outcome_uses_effective_production_after_cash_break() -> None:
    sim = ExschoolSimulator()
    context = sim._context_with_campaign_state("r1", None)
    raw_decision = SimulationInput(
        round_id="r1",
        loan_delta=0.0,
        workers=100,
        engineers=1000,
        worker_salary=2_500.0,
        engineer_salary=4_700.0,
        management_investment=0.0,
        quality_investment=0.0,
        research_investment=0.0,
        products_planned=100_000,
        market_decisions=_market_decisions(subscribed_shanghai=False),
    )

    effective = sim._apply_cash_break_to_decision(
        raw_decision,
        context,
        starting_cash=float(context["starting_cash"]),
        starting_debt=float(context["starting_debt"]),
    )

    assert effective.products_planned < raw_decision.products_planned

    opponent = sim._fixed_decision_for_team("r1", "7")
    assert opponent is not None
    opponent_context = sim._context_for_company_state("r1", "7", None)
    _, team_frames = sim._simulate_market_multiplayer(
        effective_decisions_by_team={"13": effective, "7": opponent},
        contexts_by_team={"13": context, "7": opponent_context},
    )
    outcome = sim._financial_outcome_for_team(
        "13",
        team_frames["13"],
        effective,
        context,
        float(context["starting_cash"]),
        float(context["starting_debt"]),
    )

    assert outcome["new_components"] == effective.products_planned * 7
    assert outcome["components_used"] == effective.products_planned * 7
    assert outcome["leftover_components"] == 0.0


def test_peer_market_tables_and_export_use_displayed_outcomes() -> None:
    full_market_df = pd.DataFrame(
        [
            {
                "market": "Shanghai",
                "active_market": True,
                "final_sales": 40.0,
                "market_size": 100.0,
                "team": 13,
                "management_index": 1.0,
                "agents": 1,
                "marketing_investment": 1_000.0,
                "quality_index": 1.0,
                "price": 12_000.0,
                "predicted_theoretical_cpi": 0.8,
                "predicted_marketshare_unconstrained": 0.9,
                "population": 1_000.0,
                "penetration": 0.1,
            },
            {
                "market": "Shanghai",
                "active_market": True,
                "final_sales": 60.0,
                "market_size": 100.0,
                "team": 7,
                "management_index": 1.2,
                "agents": 2,
                "marketing_investment": 2_000.0,
                "quality_index": 1.1,
                "price": 10_000.0,
                "predicted_theoretical_cpi": 0.2,
                "predicted_marketshare_unconstrained": 0.1,
                "population": 1_000.0,
                "penetration": 0.1,
            },
        ]
    )

    peer_tables = build_peer_market_tables(full_market_df, ["Shanghai"])
    summaries = build_market_report_summaries(full_market_df, ["Shanghai"])

    assert [row["team"] for row in peer_tables["Shanghai"]] == ["7", "13"]
    assert summaries["Shanghai"]["total_sales_volume"] == 100.0
    assert summaries["Shanghai"]["avg_price"] == 10_800.0

    html = _market_summaries(
        {
            "market_report_subscriptions": ["Shanghai"],
            "peer_market_tables": peer_tables,
            "market_report_summaries": summaries,
        },
        {"markets": {"Shanghai": {"population": 999.0, "initial_penetration": 0.01}}},
    )

    assert "¥10,800" in html
    assert html.index("<td>7</td>") < html.index("<td>13</td>")


def test_simulate_includes_market_report_summaries_for_subscribed_markets() -> None:
    sim = ExschoolSimulator()
    decision = SimulationInput(
        round_id="r1",
        loan_delta=0.0,
        workers=10,
        engineers=10,
        worker_salary=2_500.0,
        engineer_salary=4_700.0,
        management_investment=0.0,
        quality_investment=0.0,
        research_investment=0.0,
        products_planned=5,
        market_decisions=_market_decisions(),
    )

    report = sim.simulate(decision)

    assert report["market_report_subscriptions"] == ["Shanghai"]
    assert "Shanghai" in report["market_report_summaries"]
    expected_total_sales = sum(row["sales_volume_exact"] for row in report["peer_market_tables"]["Shanghai"])
    assert report["market_report_summaries"]["Shanghai"]["total_sales_volume"] == expected_total_sales


def test_finance_marketing_row_matches_effective_market_marketing_after_cash_break() -> None:
    sim = ExschoolSimulator("high-intensity")
    context = sim._context_with_campaign_state("r1", None, current_home_city="Chengdu")
    payload = sim.stateful_default_payload("r1", None, current_home_city="Chengdu")
    decision = sim._build_simulation_input("r1", payload, context=context, headcount_is_delta=True)

    report = sim._simulate_with_context(decision, context, mode="campaign")

    effective_marketing_total = sum(float(row["marketing_investment"]) for row in report["market_results"])
    marketing_row = next(row for row in report["finance_rows"] if row[0] == "营销投入")

    assert abs(float(marketing_row[1]) + effective_marketing_total) < 1e-6


def test_real_original_default_campaign_replays_source_financials() -> None:
    simulator = ExschoolSimulator("real-original")
    team_states = {team: None for team in simulator.team_ids}
    for round_id in simulator.available_rounds():
        context = simulator._context_for_company_state(
            round_id,
            "13",
            team_states.get("13"),
            current_home_city="Shanghai",
        )
        payload = simulator.stateful_default_payload(round_id, team_states.get("13"), current_home_city="Shanghai")
        decision = simulator._build_simulation_input(round_id, payload, context=context, headcount_is_delta=True)
        report, team_states = simulator._simulate_multiplayer_report(
            decision,
            context,
            mode="campaign",
            team_states=team_states,
        )
        source = simulator._fixed_round_summary_row(round_id, "13")

        assert source is not None
        assert report["key_metrics"]["销售收入"] == float(source["sales_revenue_source"])
        assert report["ending_cash"] == float(source["ending_cash_est"])
        assert report["ending_debt"] == float(source["ending_debt_est"])
        assert report["market_report_source"]["mode"] == "real-original-replay"

        source_market_rows = simulator.market_df[
            (simulator.market_df["round"] == round_id)
            & (simulator.market_df["team"].astype(str) == "13")
        ]
        source_by_market = {str(row["market"]): row for _, row in source_market_rows.iterrows()}
        replay_by_market = {str(row["market"]): row for row in report["market_results"]}
        for market, source_market in source_by_market.items():
            replay_market = replay_by_market[market]
            assert replay_market["sales_volume"] == float(source_market["sales_volume"])
            assert replay_market["market_share"] == float(source_market["market_share"])
            assert replay_market["price"] == float(source_market["price"])
            assert replay_market["agents_after"] == int(source_market["agents"])
            assert replay_market["marketing_investment"] == float(source_market["marketing_investment"])


def test_high_intensity_wuxi_default_opponents_do_not_lose_money() -> None:
    simulator = ExschoolSimulator("high-intensity")
    team_states = {team: None for team in simulator.team_ids}
    for round_id in simulator.available_rounds():
        context = simulator._context_for_company_state(
            round_id,
            "13",
            team_states.get("13"),
            current_home_city="Wuxi",
        )
        payload = simulator.stateful_default_payload(round_id, team_states.get("13"), current_home_city="Wuxi")
        decision = simulator._build_simulation_input(round_id, payload, context=context, headcount_is_delta=True)
        report, team_states = simulator._simulate_multiplayer_report(
            decision,
            context,
            mode="campaign",
            team_states=team_states,
        )

        opponents = [row for row in report["all_company_standings"] if str(row["team"]) != "13"]
        assert opponents
        assert all(float(row["net_profit"]) >= 0 for row in opponents)
        assert all(float(row["net_assets"]) >= 0 for row in opponents)
        assert all(float(row["sales_revenue"]) > 0 for row in opponents)
