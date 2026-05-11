from exschool_game.engine import ExschoolSimulator, MARKET_REPORT_SUBSCRIPTION_COST


EXPECTED_SUBSCRIPTIONS_BY_ROUND = {
    "r1": {"Shanghai", "Chengdu", "Wuhan"},
    "r2": {"Shanghai", "Chengdu", "Wuhan"},
    "r3": {"Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"},
    "r4": {"Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"},
}


def test_stateful_default_payload_market_report_defaults_follow_round_activity() -> None:
    simulator = ExschoolSimulator("high-intensity")

    for round_id, expected in EXPECTED_SUBSCRIPTIONS_BY_ROUND.items():
        payload = simulator.stateful_default_payload(round_id, None, current_home_city="Shanghai")
        subscribed = {
            market for market, values in payload["markets"].items() if bool(values["subscribed_market_report"])
        }

        assert subscribed == expected
        assert len(subscribed) * MARKET_REPORT_SUBSCRIPTION_COST == simulator.round_contexts[round_id]["market_report_cost"]


def test_fixed_opponent_market_report_defaults_follow_round_activity() -> None:
    simulator = ExschoolSimulator("high-intensity")

    for round_id, expected in EXPECTED_SUBSCRIPTIONS_BY_ROUND.items():
        decision = simulator._fixed_decision_for_team(round_id, "7")

        assert decision is not None
        subscribed = {
            market for market, values in decision.market_decisions.items() if values.subscribed_market_report
        }

        assert subscribed == expected
        assert len(subscribed) * MARKET_REPORT_SUBSCRIPTION_COST == simulator.round_contexts[round_id]["market_report_cost"]
