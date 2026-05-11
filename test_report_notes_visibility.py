import exschool_game.app as app_module
from exschool_game.engine import get_simulator

from test_exschool_game_modes import _advance_active_round, _register_logged_in_client, _start_game, _submit_active_round


def test_submitted_round_report_page_surfaces_explanatory_notes() -> None:
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Notes Co")

    _submit_active_round(client, user)
    response = client.get("/game", follow_redirects=True)

    assert response.status_code == 200
    assert "Report Notes" in response.text
    assert "Team 13 使用当前输入" in response.text
    assert "市场报告费用按订阅城市数 × ¥200,000 计算" in response.text


def test_report_and_final_pages_surface_home_city_after_setup(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "_prime_report_image_cache", lambda html, client_id=None: None)
    client, user = _register_logged_in_client()
    _start_game(client, "/single-fixed/start", company_name="Home City Co")

    _submit_active_round(client, user)
    report_response = client.get("/game", follow_redirects=True)

    assert report_response.status_code == 200
    assert "主场城市：上海" in report_response.text
    assert "影响财务、材料与仓储参数" in report_response.text

    for _round_id in get_simulator("high-intensity").available_rounds()[1:]:
        _advance_active_round(client, user)
        _submit_active_round(client, user)
    final_response_text = _advance_active_round(client, user)

    assert "Home City Co 最终总结" in final_response_text
    assert "主场城市：上海" in final_response_text
    assert "影响财务、材料与仓储参数" in final_response_text
