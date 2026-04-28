from exschool_game.campaign_support import build_campaign_state, build_simulation_input, next_campaign_state, payload_for_context


def test_build_simulation_input_applies_headcount_delta() -> None:
    decision = build_simulation_input(
        visible_markets={"Shanghai"},
        round_id="r1",
        raw={
            "round_id": "r1",
            "loan_delta": 0,
            "workers": 5,
            "engineers": -2,
            "worker_salary": 3000,
            "engineer_salary": 5000,
            "management_investment": 0,
            "quality_investment": 0,
            "research_investment": 0,
            "products_planned": 10,
            "markets": {"Shanghai": {"agent_change": 1, "marketing_investment": 2, "price": 3, "subscribed_market_report": True}},
        },
        context={"workers_actual": 10, "engineers_actual": 8},
        headcount_is_delta=True,
    )
    assert decision.workers == 15
    assert decision.engineers == 6


def test_payload_for_context_returns_delta_headcount_values() -> None:
    payload = payload_for_context(
        "r2",
        {
            "actual_loan_delta": 0,
            "workers_actual": 10,
            "engineers_actual": 8,
            "payload_workers": 15,
            "payload_engineers": 6,
            "worker_salary_actual": 3000,
            "engineer_salary_actual": 5000,
            "payload_worker_salary": 3200,
            "payload_engineer_salary": 5200,
            "management_investment_actual": 0,
            "quality_investment_actual": 0,
            "research_investment_actual": 0,
            "products_produced_actual": 100,
            "visible_markets": ["Shanghai"],
            "market_defaults": {
                "Shanghai": {
                    "actual_marketing_investment": 1000,
                    "actual_price": 20000,
                }
            },
        },
    )
    assert payload["workers"] == 5
    assert payload["engineers"] == -2
    assert payload["worker_salary"] == 3200
    assert payload["engineer_salary"] == 5200


def test_next_campaign_state_carries_inventory_and_patents() -> None:
    state = next_campaign_state(
        report={
            "ending_cash": 100.0,
            "ending_debt": 20.0,
            "management_index": 1.0,
            "quality_index": 2.0,
            "market_results": [{"market": "Shanghai", "agents_after": 3}],
            "storage_summary": [{"item": "Components", "capacity_after": 10.0}, {"item": "Products", "capacity_after": 5.0}],
            "component_inventory_end": 7.0,
            "product_inventory_end": 4.0,
            "active_patents_next_round": 2,
            "accumulated_research_investment_next_round": 500.0,
            "worker_plan": {"working": 11, "average_salary": 3000.0, "next_recent": 1, "next_mature": 2, "next_experienced": 8},
            "engineer_plan": {"working": 9, "average_salary": 5000.0, "next_recent": 1, "next_mature": 1, "next_experienced": 7},
        },
        decision=build_simulation_input(
            visible_markets={"Shanghai"},
            round_id="r1",
            raw={
                "round_id": "r1",
                "loan_delta": 0,
                "workers": 11,
                "engineers": 9,
                "worker_salary": 3000,
                "engineer_salary": 5000,
                "management_investment": 0,
                "quality_investment": 0,
                "research_investment": 0,
                "products_planned": 0,
                "markets": {"Shanghai": {"agent_change": 0, "marketing_investment": 0, "price": 20000, "subscribed_market_report": True}},
            },
        ),
        state=None,
    )
    assert state.component_inventory == 7.0
    assert state.product_inventory == 4.0
    assert state.active_patents == 2
    assert state.market_agents_after["Shanghai"] == 3


def test_build_campaign_state_initializes_indices_and_zero_carry() -> None:
    state = build_campaign_state(
        context={
            "starting_cash": 100.0,
            "starting_debt": 20.0,
            "workers_actual": 10,
            "engineers_actual": 5,
            "worker_salary_actual": 3000.0,
            "engineer_salary_actual": 5000.0,
            "management_investment_actual": 150.0,
            "quality_investment_actual": 100.0,
            "products_produced_actual": 20.0,
            "visible_markets": ["Shanghai"],
            "market_defaults": {"Shanghai": {"previous_agents": 1}},
        },
        initial_worker_avg=2800.0,
        initial_engineer_avg=4800.0,
    )
    assert state.previous_management_index == 10.0
    assert state.previous_quality_index == 5.0
    assert state.component_inventory == 0.0
    assert state.active_patents == 0
