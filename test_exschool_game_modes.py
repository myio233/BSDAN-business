from __future__ import annotations

import json
import os
import re
import time

from fastapi.testclient import TestClient

import exschool_game.app as app_module
import exschool_game.auth_client as auth_client_module
from exschool_game.app import app, _compact_all_company_round_point, _default_session, _report_summary, _state_from_session
from exschool_game.auth_store import AuthStore, auth_store
from exschool_game.data_loader import describe_fixed_decision_source
from exschool_game.email_code_service import EmailCodeService
from exschool_game.engine import get_simulator
from exschool_game.request_guard_service import request_guard_service
from exschool_game.user_store import UserGameStore, user_game_store


def test_real_original_mode_uses_reconstructed_workbook() -> None:
    simulator = get_simulator("real-original")
    assert len(simulator.fixed_decisions_df) == 92
    assert len(simulator.fixed_round_summary_df) == 92
    assert "13" in simulator.team_ids


def test_high_intensity_mode_uses_smart_workbook_and_no_real_summary() -> None:
    simulator = get_simulator("high-intensity")
    real_original = get_simulator("real-original")
    source = describe_fixed_decision_source("high-intensity")
    assert len(simulator.fixed_decisions_df) == 92
    assert simulator.fixed_round_summary_df.empty
    assert source["source_path"].endswith("all_companies_numeric_decisions_smart.xlsx")
    merged = simulator.fixed_decisions_df.merge(
        real_original.fixed_decisions_df,
        on=["team", "round_id"],
        suffixes=("_high", "_real"),
    )
    non_team13 = merged[merged["team"].astype(str) != "13"].copy()
    differing_rows = non_team13[
        (non_team13["products_planned_high"] != non_team13["products_planned_real"])
        | (non_team13["workers_high"] != non_team13["workers_real"])
        | (non_team13["engineers_high"] != non_team13["engineers_real"])
        | (non_team13["management_investment_high"] != non_team13["management_investment_real"])
    ]
    assert not differing_rows.empty


def test_high_intensity_default_r1_is_outcompeted_by_smart_opponents() -> None:
    simulator = get_simulator("high-intensity")
    for home_city in ["Shanghai", "Chengdu"]:
        context = simulator._context_with_campaign_state("r1", None, current_home_city=home_city)
        payload = simulator.stateful_default_payload("r1", None, current_home_city=home_city)
        decision = simulator._build_simulation_input("r1", payload, context=context, headcount_is_delta=True)
        report = simulator._simulate_with_context(decision, context, mode="campaign")

        top_opponent = report["all_company_standings"][0]
        assert int(report["key_metrics"]["预计排名"]) > 1
        assert str(top_opponent["team"]) != "13"
        assert float(top_opponent["net_profit"]) > 1_000_000.0
        assert float(top_opponent["sales_revenue"]) > 14_000_000.0


def test_high_intensity_preserves_team13_round_one_default_payload() -> None:
    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Wuxi")

    assert payload["loan_delta"] == 5_000_000.0
    assert payload["products_planned"] == 3015
    assert payload["workers"] == 879
    assert payload["engineers"] == 335
    assert payload["markets"]["Shanghai"]["agent_change"] == 1
    assert payload["markets"]["Chengdu"]["agent_change"] == 1
    assert payload["markets"]["Wuhan"]["agent_change"] == 1


def test_high_intensity_default_campaign_has_five_elite_and_saturation_opponents() -> None:
    simulator = get_simulator("high-intensity")
    elite_teams = {"1", "2", "8", "18", "19"}
    for home_city in ["Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"]:
        team_states = {team: None for team in simulator.team_ids}
        for round_id in simulator.available_rounds():
            context = simulator._context_for_company_state(
                round_id,
                "13",
                team_states.get("13"),
                current_home_city=home_city,
            )
            payload = simulator.stateful_default_payload(round_id, team_states.get("13"), current_home_city=home_city)
            decision = simulator._build_simulation_input(round_id, payload, context=context, headcount_is_delta=True)
            report, team_states = simulator._simulate_multiplayer_report(
                decision,
                context,
                mode="campaign",
                team_states=team_states,
            )

            standings_by_team = {str(row["team"]): row for row in report["all_company_standings"]}
            elite_rows = [standings_by_team[team] for team in elite_teams]
            if round_id != "r1":
                assert max(float(row["net_assets"]) for row in elite_rows) > 30_000_000.0

        final_standings = {str(row["team"]): row for row in report["all_company_standings"]}
        elite_final_assets = [float(final_standings[team]["net_assets"]) for team in elite_teams]
        assert min(elite_final_assets) > 45_000_000.0
        assert max(elite_final_assets) > 48_000_000.0

        saturator_rows = [
            row
            for team, row in final_standings.items()
            if team not in elite_teams and team != "13"
        ]
        assert len(saturator_rows) >= 10
        assert sum(float(row["sales_revenue"]) > 10_000_000.0 for row in saturator_rows) >= 15


def test_player_context_applies_home_city_finance_and_material_parameters() -> None:
    simulator = get_simulator("high-intensity")
    context = simulator._context_with_campaign_state("r1", None, current_home_city="Chengdu")
    assert context["current_home_city"] == "Chengdu"
    assert context["interest_rate"] == 0.036
    assert context["component_material_price"] == 258.0
    assert context["product_material_price"] == 630.0
    assert context["component_storage_unit_cost"] == 24.0
    assert context["product_storage_unit_cost"] == 100.0
    assert context["loan_limit"] == 3_500_000.0


def test_default_round_one_payload_has_active_agents() -> None:
    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    positive_markets = [market for market, values in payload["markets"].items() if int(values["agent_change"]) > 0]
    assert positive_markets


def test_default_round_one_payload_does_not_force_all_market_reports_on() -> None:
    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    subscribed_markets = {market for market, values in payload["markets"].items() if values["subscribed_market_report"]}
    assert subscribed_markets == {"Chengdu", "Shanghai", "Wuhan"}


def test_fixed_opponent_market_report_selection_falls_back_to_round_activity_when_selected_columns_overstate_spend() -> None:
    simulator = get_simulator("high-intensity")
    decision = simulator._fixed_decision_for_team("r1", "1")
    assert decision is not None
    subscribed_markets = {market for market, values in decision.market_decisions.items() if values.subscribed_market_report}
    assert subscribed_markets == {"Chengdu", "Shanghai", "Wuhan"}


def test_loan_delta_is_clamped_by_cash_when_repaying() -> None:
    simulator = get_simulator("high-intensity")
    clamped = simulator._clamp_loan_delta(
        -5_000_000.0,
        {
            "starting_cash": 1_000_000.0,
            "starting_debt": 5_000_000.0,
            "loan_limit": 5_000_000.0,
        },
    )
    assert clamped == -1_000_000.0


def test_user_store_migrates_legacy_single_session_and_returns_deep_copies(tmp_path) -> None:
    legacy_session = {
        "game_id": "legacy-game",
        "company_name": "Legacy Co",
        "single_player_mode": "high-intensity",
        "current_round": "r2",
        "started": True,
        "reports": [{"round_id": "r1", "key_metrics": {"销售收入": 1}}],
    }
    path = tmp_path / "user_games.json"
    path.write_text(
        json.dumps(
            {
                "users": {
                    "legacy-user": {
                        "profile": {"client_id": "legacy-user", "name": "Legacy", "email": "legacy@example.com"},
                        "active_game_session": legacy_session,
                        "history": [],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = UserGameStore(path)

    loaded = store.get_active_game_session("legacy-user")
    assert loaded is not None
    assert loaded["game_id"] == "legacy-game"
    assert len(store.list_active_game_sessions("legacy-user")) == 1

    loaded["reports"][0]["round_id"] = "mutated"
    reloaded = store.get_active_game_session("legacy-user")
    assert reloaded is not None
    assert reloaded["reports"][0]["round_id"] == "r1"


def test_archive_completed_game_removes_only_target_active_session(tmp_path) -> None:
    store = UserGameStore(tmp_path / "user_games.json")
    user = {"client_id": "user-1", "name": "Tester", "email": "tester@example.com"}
    simulator = get_simulator("high-intensity")
    session_a = _default_session(simulator)
    session_a["game_id"] = "game-a"
    session_a["company_name"] = "Alpha"
    session_a["started"] = True
    session_b = _default_session(simulator)
    session_b["game_id"] = "game-b"
    session_b["company_name"] = "Beta"
    session_b["started"] = True
    store.save_active_game_session(user, session_a)
    store.save_active_game_session(user, session_b)

    store.archive_completed_game(user, session_a, [{"round_id": "r4", "key_metrics": {"销售收入": 0}}])

    active_sessions = store.list_active_game_sessions("user-1")
    history = store.list_history("user-1")
    assert [item["game_id"] for item in active_sessions] == ["game-b"]
    assert history[0]["game_id"] == "game-a"


def _register_logged_in_client() -> tuple[TestClient, dict[str, str]]:
    _clear_request_guard_buckets()
    client = TestClient(app)
    suffix = f"{time.time_ns()}"
    name = f"test-user-{suffix}"
    email = f"{name}@example.com"
    password = "playwright-pass"
    user, _token = auth_store.register_user(name, email, password)
    csrf_token = _fetch_csrf_token(client, "/auth?mode=login")
    response = client.post(
        "/auth/login",
        data={
            "account": email,
            "password": password,
            "_csrf": csrf_token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client, user


def _clear_request_guard_buckets() -> None:
    # Keep auth/login and app-side guard state isolated across the suite.
    request_guard_service.buckets.clear()
    auth_client_module.request_guard_service.buckets.clear()
    app_module.request_guard_service.buckets.clear()


def _start_game(client: TestClient, mode_start_path: str, company_name: str = "Mode Test Co") -> None:
    csrf_token = _fetch_csrf_token(client, "/")
    response = client.post(
        mode_start_path,
        data={
            "company_name": company_name,
            "home_city": "Shanghai",
            "_csrf": csrf_token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "第 1 轮" in response.text


def _submit_active_round(client: TestClient, user: dict[str, str]) -> None:
    active_session = user_game_store.get_active_game_session(user["client_id"])
    assert active_session is not None
    simulator = get_simulator(str(active_session.get("single_player_mode", "high-intensity")))
    payload = simulator.stateful_default_payload(
        str(active_session["current_round"]),
        _state_from_session(active_session.get("campaign_state")),
        current_home_city=str(active_session.get("home_city", "Shanghai")),
    )
    response = client.post(
        "/game/submit",
        data=_round_form_data(
            payload,
            csrf_token=_fetch_csrf_token(client, "/game"),
            game_id=str(active_session.get("game_id", "")) or None,
        ),
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "本轮财报" in response.text


def _advance_active_round(client: TestClient, user: dict[str, str]) -> str:
    active_session = user_game_store.get_active_game_session(user["client_id"])
    assert active_session is not None
    response = client.post(
        "/game/next",
        data={
            "_csrf": _fetch_csrf_token(client, "/game"),
            "game_id": str(active_session.get("game_id", "")),
        },
        follow_redirects=False,
    )
    assert response.status_code == 200
    return response.text


def _fetch_csrf_token(client: TestClient, path: str) -> str:
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _home_active_game_href(html: str) -> str | None:
    match = re.search(r'class="home-minimal-button" href="([^"]+)"', html)
    return match.group(1) if match else None


def _round_form_data(payload: dict[str, object], *, csrf_token: str, game_id: str | None = None) -> dict[str, str]:
    data: dict[str, str] = {
        "round_id": str(payload["round_id"]),
        "submit_mode": "manual-confirmed",
        "loan_delta": str(payload["loan_delta"]),
        "workers": str(payload["workers"]),
        "engineers": str(payload["engineers"]),
        "worker_salary": str(payload["worker_salary"]),
        "engineer_salary": str(payload["engineer_salary"]),
        "management_investment": str(payload["management_investment"]),
        "quality_investment": str(payload["quality_investment"]),
        "research_investment": str(payload["research_investment"]),
        "products_planned": str(payload["products_planned"]),
        "_csrf": csrf_token,
    }
    if game_id:
        data["game_id"] = game_id
    for market, values in dict(payload["markets"]).items():
        slug = str(market).lower()
        market_values = dict(values)
        if market_values.get("subscribed_market_report", True):
            data[f"{slug}_market_report"] = "1"
        data[f"{slug}_agent_change"] = str(market_values["agent_change"])
        data[f"{slug}_marketing_investment"] = str(market_values["marketing_investment"])
        data[f"{slug}_price"] = str(market_values["price"])
    return data


def test_single_setup_does_not_clear_existing_active_game_session() -> None:
    client, user = _register_logged_in_client()
    simulator = get_simulator("high-intensity")
    active_session = _default_session(simulator)
    active_session["company_name"] = "Saved Co"
    active_session["home_city"] = "Shanghai"
    active_session["single_player_mode"] = "high-intensity"
    active_session["started"] = True
    user_game_store.save_active_game_session(user, active_session)

    response = client.get("/single-real/setup")

    assert response.status_code == 200
    assert "真实原版竞争" in response.text
    saved_session = user_game_store.get_active_game_session(user["client_id"])
    assert saved_session is not None
    assert saved_session["started"] is True
    assert saved_session["company_name"] == "Saved Co"
    assert saved_session["single_player_mode"] == "high-intensity"


def test_generic_single_setup_does_not_reset_in_progress_round_state() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Resume Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    submit_response = client.post(
        "/game/submit",
        data=_round_form_data(payload, csrf_token=_fetch_csrf_token(client, "/game")),
        follow_redirects=False,
    )

    setup_response = client.get("/single/setup?mode=real-original")
    saved_session = user_game_store.get_active_game_session(user["client_id"])
    resume_response = client.get("/game")

    assert submit_response.status_code == 200
    assert setup_response.status_code == 200
    assert "真实原版竞争" in setup_response.text
    assert saved_session is not None
    assert saved_session["current_round"] == "r1"
    assert len(saved_session["reports"]) == 1
    assert resume_response.status_code == 200
    assert "本轮财报" in resume_response.text
    assert "预览并提交" not in resume_response.text


def test_mode_specific_start_route_persists_real_original_mode() -> None:
    client, user = _register_logged_in_client()

    response = client.get("/single-real/setup")

    assert response.status_code == 200
    assert "真实原版竞争" in response.text
    _start_game(client, "/single-real/start", company_name="Real Original Co")
    saved_session = user_game_store.get_active_game_session(user["client_id"])
    assert saved_session is not None
    assert saved_session["started"] is True
    assert saved_session["single_player_mode"] == "real-original"


def test_submitted_round_reopens_report_page_instead_of_form() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Submit Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    csrf_token = _fetch_csrf_token(client, "/game")
    submit_response = client.post("/game/submit", data=_round_form_data(payload, csrf_token=csrf_token), follow_redirects=False)

    assert submit_response.status_code == 200
    assert "本轮财报" in submit_response.text

    resume_response = client.get("/game")
    duplicate_submit_response = client.post("/game/submit", data=_round_form_data(payload, csrf_token=csrf_token), follow_redirects=False)
    saved_session = user_game_store.get_active_game_session(user["client_id"])

    assert resume_response.status_code == 200
    assert "本轮财报" in resume_response.text
    assert "预览并提交" not in resume_response.text
    assert duplicate_submit_response.status_code == 200
    assert "本轮财报" in duplicate_submit_response.text
    assert saved_session is not None
    assert len(saved_session["reports"]) == 1
    assert len(saved_session["all_company_rounds"]) == 1
    assert saved_session["single_player_mode"] == "high-intensity"


def test_single_start_validation_preserves_company_name_and_home_city() -> None:
    client, _user = _register_logged_in_client()

    response = client.post(
        "/single-fixed/start",
        data={
            "company_name": "Keep Me Co",
            "home_city": "Atlantis",
            "_csrf": _fetch_csrf_token(client, "/single-fixed/setup"),
        },
        follow_redirects=False,
    )

    assert response.status_code == 422
    assert 'value="Keep Me Co"' in response.text
    assert '<option value="Atlantis" selected disabled>Atlantis</option>' in response.text


def test_final_next_makes_game_terminal_and_game_page_stays_out_of_round_form() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Terminal Co")

    final_response_text = ""
    simulator = get_simulator("high-intensity")
    for _round_id in simulator.available_rounds():
        _submit_active_round(client, user)
        final_response_text = _advance_active_round(client, user)

    active_session = user_game_store.get_active_game_session(user["client_id"])
    game_response = client.get("/game")

    assert "Terminal Co 最终总结" in final_response_text
    assert active_session is None
    assert game_response.status_code == 200
    assert "Terminal Co 最终总结" in game_response.text
    assert "预览并提交" not in game_response.text


def test_multiple_active_games_can_be_selected_from_home() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Alpha Co")
    first_session = user_game_store.get_active_game_session(user["client_id"])
    assert first_session is not None
    alpha_game_id = first_session["game_id"]

    client.get("/single-real/setup")
    _start_game(client, "/single-real/start", company_name="Beta Co")
    active_sessions = user_game_store.list_active_game_sessions(user["client_id"])
    beta_game_id = next(item["game_id"] for item in active_sessions if item["company_name"] == "Beta Co")

    home_response = client.get("/")
    alpha_response = client.get(f"/game?game_id={alpha_game_id}", follow_redirects=True)
    beta_response = client.get(f"/game?game_id={beta_game_id}", follow_redirects=True)

    assert home_response.status_code == 200
    assert "进行中的对局" in home_response.text
    assert "Alpha Co" in home_response.text
    assert "Beta Co" in home_response.text
    assert alpha_response.status_code == 200
    assert "Alpha Co" in alpha_response.text
    assert "高强度竞争" in alpha_response.text
    assert beta_response.status_code == 200
    assert "Beta Co" in beta_response.text
    assert "真实原版竞争" in beta_response.text


def test_game_get_with_explicit_game_id_does_not_change_selected_session() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Alpha Co")
    alpha_session = user_game_store.get_active_game_session(user["client_id"])
    assert alpha_session is not None
    alpha_game_id = str(alpha_session["game_id"])

    client.get("/single-real/setup")
    _start_game(client, "/single-real/start", company_name="Beta Co")
    beta_session = next(item for item in user_game_store.list_active_game_sessions(user["client_id"]) if item["company_name"] == "Beta Co")
    beta_game_id = str(beta_session["game_id"])

    home_before = client.get("/")
    game_response = client.get(f"/game?game_id={alpha_game_id}")
    home_after = client.get("/")

    assert home_before.status_code == 200
    assert _home_active_game_href(home_before.text) == f"http://testserver/game?game_id={beta_game_id}"
    assert game_response.status_code == 200
    assert "Alpha Co" in game_response.text
    assert home_after.status_code == 200
    assert _home_active_game_href(home_after.text) == f"http://testserver/game?game_id={beta_game_id}"


def test_submit_and_next_use_explicit_game_id_not_only_selected_session() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Alpha Co")
    alpha_session = user_game_store.get_active_game_session(user["client_id"])
    assert alpha_session is not None
    alpha_game_id = str(alpha_session["game_id"])

    client.get("/single-real/setup")
    _start_game(client, "/single-real/start", company_name="Beta Co")
    active_sessions = user_game_store.list_active_game_sessions(user["client_id"])
    beta_session = next(item for item in active_sessions if item["company_name"] == "Beta Co")
    beta_game_id = str(beta_session["game_id"])

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")

    submit_response = client.post(
        "/game/submit",
        data=_round_form_data(payload, csrf_token=_fetch_csrf_token(client, f"/game?game_id={alpha_game_id}"), game_id=alpha_game_id),
        follow_redirects=False,
    )

    refreshed_alpha = user_game_store.get_active_game_session(user["client_id"], game_id=alpha_game_id)
    refreshed_beta = user_game_store.get_active_game_session(user["client_id"], game_id=beta_game_id)

    assert submit_response.status_code == 200
    assert "本轮财报" in submit_response.text
    assert refreshed_alpha is not None
    assert len(refreshed_alpha["reports"]) == 1
    assert refreshed_beta is not None
    assert len(refreshed_beta["reports"]) == 0


def test_submit_with_missing_explicit_game_id_does_not_fallback_to_other_active_save() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Alpha Co")
    active_session = user_game_store.get_active_game_session(user["client_id"])
    assert active_session is not None

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    response = client.post(
        "/game/submit",
        data=_round_form_data(payload, csrf_token=_fetch_csrf_token(client, "/"), game_id="missing-game-id"),
        follow_redirects=False,
    )

    refreshed_session = user_game_store.get_active_game_session(user["client_id"], game_id=str(active_session["game_id"]))
    assert response.status_code == 404
    assert "未找到指定对局" in response.text
    assert refreshed_session is not None
    assert len(refreshed_session["reports"]) == 0


def test_history_detail_reopens_completed_summary() -> None:
    client, user = _register_logged_in_client()
    simulator = get_simulator("high-intensity")
    session = _default_session(simulator)
    session["company_name"] = "History Co"
    session["home_city"] = "Shanghai"
    session["single_player_mode"] = "high-intensity"
    session["started"] = True

    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    context = simulator._context_with_campaign_state("r1", None, current_home_city="Shanghai")
    decision = simulator._build_simulation_input("r1", payload, context=context, headcount_is_delta=True)
    report = simulator._simulate_with_context(decision, context, mode="campaign")
    report_summary = _report_summary(report)
    session["reports"] = [report_summary]
    session["all_company_rounds"] = [_compact_all_company_round_point(report)]

    user_game_store.save_active_game_session(user, session)
    user_game_store.archive_completed_game(user, session, [report_summary])

    response = client.get(f"/history/{session['game_id']}")

    assert response.status_code == 200
    assert "History Co 最终总结" in response.text
    assert "历史结果" in response.text
    assert "高强度竞争" in response.text


def test_round_page_surfaces_carry_over_state_and_market_evidence() -> None:
    client, user = _register_logged_in_client()
    simulator = get_simulator("high-intensity")
    session = _default_session(simulator)
    session["company_name"] = "Transparency Co"
    session["home_city"] = "Shanghai"
    session["single_player_mode"] = "high-intensity"
    session["started"] = True
    session["current_round"] = "r2"
    session["campaign_state"] = {
        "current_cash": 18_250_000.0,
        "current_debt": 2_100_000.0,
        "workers": 34,
        "engineers": 12,
        "worker_salary": 9_200.0,
        "engineer_salary": 18_500.0,
        "market_agents_after": {"Shanghai": 3, "Chengdu": 2, "Wuhan": 1, "Wuxi": 0, "Ningbo": 4},
        "previous_management_index": 58.75,
        "previous_quality_index": 3.4,
        "worker_avg_salary": 9_000.0,
        "engineer_avg_salary": 18_000.0,
        "worker_recent": 8,
        "worker_mature": 16,
        "worker_experienced": 10,
        "engineer_recent": 2,
        "engineer_mature": 6,
        "engineer_experienced": 4,
        "component_capacity": 4_200.0,
        "product_capacity": 560.0,
        "component_inventory": 315.0,
        "product_inventory": 44.0,
        "active_patents": 2,
        "accumulated_research_investment": 1_280_000.0,
        "last_round_id": "r1",
    }
    user_game_store.save_active_game_session(user, session)

    round_context = simulator._context_with_campaign_state(
        "r2",
        _state_from_session(session["campaign_state"]),
        current_home_city="Shanghai",
    )
    shanghai = round_context["market_defaults"]["Shanghai"]

    response = client.get("/game")

    assert response.status_code == 200
    assert "续接状态" in response.text
    assert "续接自 R1" in response.text
    assert "零件 315" in response.text
    assert "成品 44" in response.text
    assert "零件 4,200" in response.text
    assert "成品 560" in response.text
    assert "2 项专利" in response.text
    assert "累计研发 ¥1,280,000" in response.text
    assert "上一轮管理指数" in response.text
    assert "58.75" in response.text
    assert "上一轮质量指数 3.40" in response.text
    assert "已知市场参考" in response.text
    assert f"上轮代理 {int(shanghai['previous_agents'])}" in response.text
    assert f"参考价格 ¥{float(shanghai['actual_price']):,.0f}" in response.text
    assert f"参考销量 {float(shanghai['actual_sales_volume']):,.0f}" in response.text
    assert f"参考份额 {float(shanghai['actual_market_share']):.1%}" in response.text


def test_auth_email_code_rejects_invalid_json() -> None:
    client = TestClient(app)

    response = client.post(
        "/auth/email-code",
        content="{invalid-json",
        headers={"content-type": "application/json", "x-csrf-token": _fetch_csrf_token(client, "/auth?mode=register")},
    )

    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_auth_email_code_requires_csrf_token() -> None:
    client = TestClient(app)

    response = client.post(
        "/auth/email-code",
        json={"email": "tester@example.com", "purpose": "register"},
    )

    assert response.status_code == 403
    assert response.json()["ok"] is False


def test_auth_email_code_returns_503_when_delivery_service_fails(monkeypatch) -> None:
    client = TestClient(app)
    monkeypatch.setattr(
        auth_client_module.email_code_service,
        "send_code",
        lambda _email, _purpose: (_ for _ in ()).throw(RuntimeError("smtp down")),
    )

    response = client.post(
        "/auth/email-code",
        json={"email": "tester@example.com", "purpose": "register"},
        headers={"x-csrf-token": _fetch_csrf_token(client, "/auth?mode=register")},
    )

    assert response.status_code == 503
    assert response.json() == {"ok": False, "detail": "验证码发送失败，请稍后重试。"}


def test_round_defaults_invalid_round_returns_404() -> None:
    client = TestClient(app)

    response = client.get("/api/rounds/r999/defaults")

    assert response.status_code == 404
    assert response.json()["ok"] is False


def test_submit_round_rejects_manual_submission_after_deadline() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Deadline Co")
    active_session = user_game_store.get_active_game_session(user["client_id"])
    assert active_session is not None
    active_session["round_started_at_ms_by_round"] = {"r1": int(time.time() * 1000) - ((40 * 60 + 1) * 1000)}
    user_game_store.save_active_game_session(user, active_session)

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    csrf_token = _fetch_csrf_token(client, "/game")
    response = client.post("/game/submit", data=_round_form_data(payload, csrf_token=csrf_token), follow_redirects=False)

    assert response.status_code == 409
    assert "已截止" in response.text


def test_submit_round_requires_csrf_token() -> None:
    client, _user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="CSRF Guard Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    data = _round_form_data(payload, csrf_token="invalid-token")
    response = client.post("/game/submit", data=data, follow_redirects=False)

    assert response.status_code == 403
    assert "请求令牌无效" in response.text


def test_submit_round_invalid_numeric_input_returns_422_instead_of_500() -> None:
    client, _user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Invalid Input Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    data = _round_form_data(payload, csrf_token=_fetch_csrf_token(client, "/game"))
    data["workers"] = "oops"
    data["shanghai_marketing_investment"] = "broken-budget"

    response = client.post("/game/submit", data=data, follow_redirects=False)

    assert response.status_code == 422
    assert "oops" in response.text
    assert "broken-budget" in response.text


def test_submit_round_allows_timeout_auto_submit_within_grace() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Timeout Auto Co")
    active_session = user_game_store.get_active_game_session(user["client_id"])
    assert active_session is not None
    active_session["round_started_at_ms_by_round"] = {"r1": int(time.time() * 1000) - ((40 * 60 + 5) * 1000)}
    user_game_store.save_active_game_session(user, active_session)

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    csrf_token = _fetch_csrf_token(client, "/game")
    data = _round_form_data(payload, csrf_token=csrf_token)
    data["submit_mode"] = "timeout-auto"
    response = client.post("/game/submit", data=data, follow_redirects=False)

    assert response.status_code == 200
    assert "本轮财报" in response.text


def test_report_image_requires_cache_key_not_raw_html(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_prime_report_image_cache", lambda html, client_id=None: None)
    client, _user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Export Guard Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    csrf_token = _fetch_csrf_token(client, "/game")
    submit_response = client.post("/game/submit", data=_round_form_data(payload, csrf_token=csrf_token), follow_redirects=False)

    assert submit_response.status_code == 200
    response = client.post(
        "/game/report-image",
        json={"html": "<html></html>"},
        headers={"x-csrf-token": csrf_token},
    )

    assert response.status_code == 400


def test_report_image_renders_from_server_side_cache_key(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_prime_report_image_cache", lambda html, client_id=None: None)
    monkeypatch.setattr(app_module, "_render_report_image_bytes", lambda html: b"png-bytes")
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Export OK Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    csrf_token = _fetch_csrf_token(client, "/game")
    submit_response = client.post("/game/submit", data=_round_form_data(payload, csrf_token=csrf_token), follow_redirects=False)

    assert submit_response.status_code == 200
    session = user_game_store.get_active_game_session(user["client_id"])
    assert session is not None
    latest_report = dict(session["latest_report_detail"])
    html, cache_key = app_module._report_export_artifacts(simulator, session, latest_report)
    assert html

    response = client.post(
        "/game/report-image",
        json={"cache_key": cache_key, "game_id": session["game_id"], "round_id": latest_report["round_id"]},
        headers={"x-csrf-token": csrf_token},
    )

    assert response.status_code == 200
    assert response.content == b"png-bytes"
    assert response.headers["content-type"] == "image/png"


def test_report_image_cached_rejects_shared_global_cache_file(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "REPORT_IMAGE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_prime_report_image_cache", lambda html, client_id=None: None)
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Scoped Cache Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    csrf_token = _fetch_csrf_token(client, "/game")
    submit_response = client.post("/game/submit", data=_round_form_data(payload, csrf_token=csrf_token), follow_redirects=False)

    assert submit_response.status_code == 200
    session = user_game_store.get_active_game_session(user["client_id"])
    assert session is not None
    latest_report = dict(session["latest_report_detail"])
    _html, cache_key = app_module._report_export_artifacts(simulator, session, latest_report)
    (tmp_path / f"{cache_key}.png").write_bytes(b"shared-cache-bytes")

    response = client.get(f"/game/report-image/{cache_key}?game_id={session['game_id']}")

    assert response.status_code == 204


def test_report_image_cached_get_does_not_change_selected_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "REPORT_IMAGE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_prime_report_image_cache", lambda html, client_id=None: None)
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Alpha Co")
    alpha_session = user_game_store.get_active_game_session(user["client_id"])
    assert alpha_session is not None
    alpha_game_id = str(alpha_session["game_id"])

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    submit_response = client.post(
        "/game/submit",
        data=_round_form_data(payload, csrf_token=_fetch_csrf_token(client, "/game")),
        follow_redirects=False,
    )
    assert submit_response.status_code == 200

    session_with_report = user_game_store.get_active_game_session(user["client_id"], game_id=alpha_game_id)
    assert session_with_report is not None
    latest_report = dict(session_with_report["latest_report_detail"])
    _html, cache_key = app_module._report_export_artifacts(simulator, session_with_report, latest_report)

    client.get("/single-real/setup")
    _start_game(client, "/single-real/start", company_name="Beta Co")
    beta_session = next(item for item in user_game_store.list_active_game_sessions(user["client_id"]) if item["company_name"] == "Beta Co")
    beta_game_id = str(beta_session["game_id"])

    home_before = client.get("/")
    image_response = client.get(f"/game/report-image/{cache_key}?game_id={alpha_game_id}")
    home_after = client.get("/")

    assert home_before.status_code == 200
    assert _home_active_game_href(home_before.text) == f"http://testserver/game?game_id={beta_game_id}"
    assert image_response.status_code == 204
    assert home_after.status_code == 200
    assert _home_active_game_href(home_after.text) == f"http://testserver/game?game_id={beta_game_id}"


def test_report_image_cached_download_query_sets_attachment_header(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "REPORT_IMAGE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_prime_report_image_cache", lambda html, client_id=None: None)
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Download Header Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    submit_response = client.post(
        "/game/submit",
        data=_round_form_data(payload, csrf_token=_fetch_csrf_token(client, "/game")),
        follow_redirects=False,
    )
    assert submit_response.status_code == 200

    session = user_game_store.get_active_game_session(user["client_id"])
    assert session is not None
    latest_report = dict(session["latest_report_detail"])
    _html, cache_key = app_module._report_export_artifacts(simulator, session, latest_report)
    cache_path = app_module._report_image_cache_path_from_key(cache_key, client_id=user["client_id"])
    cache_path.write_bytes(b"cached-png")

    response = client.get(f"/game/report-image/{cache_key}?game_id={session['game_id']}&download=1")

    assert response.status_code == 200
    assert response.content == b"cached-png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'attachment; filename="report.png"'


def test_report_image_cached_download_query_renders_when_cache_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "REPORT_IMAGE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "_prime_report_image_cache", lambda html, client_id=None: None)
    monkeypatch.setattr(app_module, "_render_report_image_bytes", lambda html: b"generated-png")
    _clear_request_guard_buckets()
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Download Generate Co")

    simulator = get_simulator("high-intensity")
    payload = simulator.stateful_default_payload("r1", None, current_home_city="Shanghai")
    submit_response = client.post(
        "/game/submit",
        data=_round_form_data(payload, csrf_token=_fetch_csrf_token(client, "/game")),
        follow_redirects=False,
    )
    assert submit_response.status_code == 200

    session = user_game_store.get_active_game_session(user["client_id"])
    assert session is not None
    latest_report = dict(session["latest_report_detail"])
    _html, cache_key = app_module._report_export_artifacts(simulator, session, latest_report)
    cache_path = app_module._report_image_cache_path_from_key(cache_key, client_id=user["client_id"])
    assert not cache_path.exists()

    response = client.get(f"/game/report-image/{cache_key}?game_id={session['game_id']}&download=1")

    assert response.status_code == 200
    assert response.content == b"generated-png"
    assert response.headers["content-disposition"] == 'attachment; filename="report.png"'
    assert cache_path.read_bytes() == b"generated-png"


def test_home_continue_links_include_explicit_game_id() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Alpha Co")
    first_session = user_game_store.get_active_game_session(user["client_id"])
    assert first_session is not None

    client.get("/single-real/setup")
    _start_game(client, "/single-real/start", company_name="Beta Co")
    beta_session = next(item for item in user_game_store.list_active_game_sessions(user["client_id"]) if item["company_name"] == "Beta Co")

    response = client.get("/")

    assert response.status_code == 200
    assert f"/game?game_id={beta_session['game_id']}" in response.text
    assert f"/game?game_id={first_session['game_id']}" in response.text


def test_html_responses_include_security_headers() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_auth_store_recovers_from_backup_and_clears_load_error(tmp_path) -> None:
    path = tmp_path / "auth_users.json"
    backup_path = path.with_suffix(".json.bak")
    primary_payload = {"user-1": {"client_id": "user-1", "name": "Broken", "email": "broken@example.com"}}
    backup_payload = {
        "user-2": {
            "client_id": "user-2",
            "name": "Recovered",
            "email": "recovered@example.com",
            "password_salt": "salt",
            "password_hash": "hash",
            "enabled": True,
            "auth_token": None,
            "auth_token_created_at": None,
            "created_at": 1.0,
            "updated_at": 1.0,
        }
    }
    path.write_text("{broken", encoding="utf-8")
    backup_path.write_text(json.dumps(backup_payload, ensure_ascii=False), encoding="utf-8")

    store = AuthStore(path)

    assert store.load_error is None
    assert "user-2" in store.accounts


def test_revoked_auth_token_invalidates_existing_session() -> None:
    client, user = _register_logged_in_client()
    account = auth_store.accounts[user["client_id"]]
    account.auth_token = "revoked-token"

    response = client.get("/mode", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith("/auth")


def test_user_store_recovers_from_backup_and_clears_load_error(tmp_path) -> None:
    path = tmp_path / "user_games.json"
    backup_path = path.with_suffix(".json.bak")
    path.write_text("{broken", encoding="utf-8")
    backup_path.write_text(
        json.dumps({"users": {"user-1": {"profile": {"client_id": "user-1"}, "active_game_sessions": [], "history": []}}}, ensure_ascii=False),
        encoding="utf-8",
    )

    store = UserGameStore(path)

    assert store.load_error is None
    assert "user-1" in store.users


def test_email_code_service_only_persists_code_after_successful_send(monkeypatch) -> None:
    service = EmailCodeService()
    monkeypatch.setattr("exschool_game.email_code_service.SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("exschool_game.email_code_service.SMTP_USER", "user@example.com")
    monkeypatch.setattr("exschool_game.email_code_service.SMTP_PASSWORD", "secret")

    def fail_send(_email: str, _subject: str, _body: str) -> None:
        raise RuntimeError("smtp down")

    monkeypatch.setattr(service, "_send_mail", fail_send)
    try:
        service.send_code("tester@example.com", "register")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected send_code to propagate SMTP failure")

    assert "tester@example.com" not in service.codes
    assert "tester@example.com" in service.delivery_errors

    monkeypatch.setattr(service, "_send_mail", lambda _email, _subject, _body: None)
    cooldown = service.send_code("tester@example.com", "register")

    assert cooldown > 0
    assert "tester@example.com" in service.codes
    assert "tester@example.com" not in service.delivery_errors


def test_report_image_cache_prune_removes_expired_and_excess_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_module, "REPORT_IMAGE_CACHE_DIR", tmp_path)
    monkeypatch.setattr(app_module, "REPORT_IMAGE_CACHE_TTL_SECONDS", 10)
    monkeypatch.setattr(app_module, "REPORT_IMAGE_CACHE_MAX_FILES", 2)
    now = time.time()

    expired = tmp_path / ("a" * 64 + ".png")
    old_survivor = tmp_path / ("b" * 64 + ".png")
    new_survivor = tmp_path / ("c" * 64 + ".png")
    newest_survivor = tmp_path / ("d" * 64 + ".png")
    for path in [expired, old_survivor, new_survivor, newest_survivor]:
        path.write_bytes(b"x")
    os_times = {
        expired: now - 20,
        old_survivor: now - 9,
        new_survivor: now - 5,
        newest_survivor: now - 1,
    }
    for path, mtime in os_times.items():
        os.utime(path, (mtime, mtime))

    app_module._prune_report_image_cache(now=now)

    remaining = sorted(path.name for path in tmp_path.glob("*.png"))
    assert remaining == [new_survivor.name, newest_survivor.name]
