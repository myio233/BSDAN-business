from __future__ import annotations

import json
import re
import time

from fastapi.testclient import TestClient

import exschool_game.app as app_module
from exschool_game.app import app
from exschool_game.multiplayer_store import MultiplayerRoomStore
from test_exschool_game_modes import _fetch_csrf_token, _round_form_data


def _login_client(name: str, email: str, password: str = "playwright-pass") -> TestClient:
    client = TestClient(app)
    unique = str(time.time_ns())
    local_name = f"{name}-{unique}"
    local_email = email.replace("@", f"-{unique}@")
    app_module.auth_store.register_user(local_name, local_email, password)
    csrf_token = _fetch_csrf_token(client, "/auth?mode=login")
    response = client.post(
        "/auth/login",
        data={"account": local_email, "password": password, "_csrf": csrf_token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client


def _extract_room_id(response_text: str) -> str:
    match = re.search(r'"room_id":\s*"([^"]+)"', response_text)
    assert match is not None
    return match.group(1)


def _extract_round_payload(response_text: str) -> dict[str, object]:
    match = re.search(r'<script id="round-page-data" type="application/json">(.*?)</script>', response_text, re.S)
    assert match is not None
    return json.loads(match.group(1))


def test_multiplayer_room_create_join_ready_start_assigns_strongest_teams(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "multiplayer_room_store", MultiplayerRoomStore(tmp_path / "multiplayer_rooms.json"))
    host = _login_client("Host", "host@example.com")
    guest = _login_client("Guest", "guest@example.com")

    create_response = host.post(
        "/multi/rooms",
        data={
            "_csrf": _fetch_csrf_token(host, "/multi/setup"),
            "seat_limit": "2",
            "bot_count": "1",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 303
    room_id = create_response.headers["location"].rsplit("/", 1)[-1]

    join_response = guest.post(
        f"/api/multi/rooms/{room_id}/join",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={},
    )
    assert join_response.status_code == 200

    update_home_city = guest.post(
        f"/api/multi/rooms/{room_id}/home-city",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={"home_city": "Chengdu"},
    )
    assert update_home_city.status_code == 200
    assert update_home_city.json()["current_player"]["home_city"] == "Chengdu"

    host.post(
        f"/api/multi/rooms/{room_id}/ready",
        headers={"x-csrf-token": _fetch_csrf_token(host, "/")},
        json={"ready": True},
    )
    guest.post(
        f"/api/multi/rooms/{room_id}/ready",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={"ready": True},
    )

    start_response = host.post(
        f"/api/multi/rooms/{room_id}/start",
        headers={"x-csrf-token": _fetch_csrf_token(host, "/")},
        json={},
    )
    assert start_response.status_code == 200
    snapshot = start_response.json()
    assert snapshot["status"] == "active"
    assert snapshot["players"][0]["team_id"] == "13"
    assert snapshot["players"][1]["team_id"] == "17"
    assert snapshot["bots"][0]["team_id"] == "22"

    host_game = host.get(f"/multi/rooms/{room_id}/game")
    guest_game = guest.get(f"/multi/rooms/{room_id}/game")
    assert host_game.status_code == 200
    assert guest_game.status_code == 200
    assert 'data-testid="multiplayer-player-status-13"' in host_game.text
    assert 'data-testid="multiplayer-player-status-17"' in guest_game.text
    assert "成都" in guest_game.text
    assert "¥3,500,000" in guest_game.text
    host_payload = _extract_round_payload(host_game.text)
    guest_payload = _extract_round_payload(guest_game.text)
    assert host_payload["currentWorkers"] == 0
    assert host_payload["currentEngineers"] == 0
    assert guest_payload["currentWorkers"] == 0
    assert guest_payload["currentEngineers"] == 0


def test_multiplayer_submit_waits_for_other_players_then_resolves_report(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "multiplayer_room_store", MultiplayerRoomStore(tmp_path / "multiplayer_rooms.json"))
    host = _login_client("Host2", "host2@example.com")
    guest = _login_client("Guest2", "guest2@example.com")

    create_response = host.post(
        "/multi/rooms",
        data={
            "_csrf": _fetch_csrf_token(host, "/multi/setup"),
            "seat_limit": "2",
            "bot_count": "0",
        },
        follow_redirects=False,
    )
    room_id = create_response.headers["location"].rsplit("/", 1)[-1]
    guest.post(
        f"/api/multi/rooms/{room_id}/join",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={},
    )
    guest.post(
        f"/api/multi/rooms/{room_id}/home-city",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={"home_city": "Chengdu"},
    )
    host.post(
        f"/api/multi/rooms/{room_id}/ready",
        headers={"x-csrf-token": _fetch_csrf_token(host, "/")},
        json={"ready": True},
    )
    guest.post(
        f"/api/multi/rooms/{room_id}/ready",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={"ready": True},
    )
    host.post(
        f"/api/multi/rooms/{room_id}/start",
        headers={"x-csrf-token": _fetch_csrf_token(host, "/")},
        json={},
    )

    host_game = host.get(f"/multi/rooms/{room_id}/game")
    host_payload = _extract_round_payload(host_game.text)
    host_submit = host.post(
        f"/multi/rooms/{room_id}/submit",
        data=_round_form_data(host_payload["initialPayload"], csrf_token=_fetch_csrf_token(host, f"/multi/rooms/{room_id}/game"), game_id=host_payload["gameId"]),
        follow_redirects=False,
    )
    assert host_submit.status_code == 303
    assert host_submit.headers["location"].endswith(f"/multi/rooms/{room_id}")

    waiting_snapshot = guest.get(f"/api/multi/rooms/{room_id}").json()
    host_row = next(item for item in waiting_snapshot["players"] if item["team_id"] == "13")
    guest_row = next(item for item in waiting_snapshot["players"] if item["team_id"] == "17")
    assert host_row["submitted_current_round"] is True
    assert guest_row["submitted_current_round"] is False

    guest_game = guest.get(f"/multi/rooms/{room_id}/game")
    guest_payload = _extract_round_payload(guest_game.text)
    guest_submit = guest.post(
        f"/multi/rooms/{room_id}/submit",
        data=_round_form_data(guest_payload["initialPayload"], csrf_token=_fetch_csrf_token(guest, f"/multi/rooms/{room_id}/game"), game_id=guest_payload["gameId"]),
        follow_redirects=False,
    )
    assert guest_submit.status_code == 303
    assert guest_submit.headers["location"].endswith(f"/multi/rooms/{room_id}/report")

    report_response = guest.get(guest_submit.headers["location"])
    assert report_response.status_code == 200
    assert "本轮财报" in report_response.text
    assert "C22" not in report_response.text
    assert "C24" not in report_response.text

    room = app_module.multiplayer_room_store.get_room_raw(room_id)
    assert room is not None
    guest_member = next(item for item in room["members"] if item["team_id"] == "17")
    latest_report = guest_member["latest_report_detail"]
    assert len(latest_report["all_company_standings"]) == 2
    assert {row["team"] for row in latest_report["all_company_standings"]} == {"13", "17"}
    assert latest_report["key_metrics"]["预计排名"] in {1, 2}
    interest_row = next(row for row in latest_report["finance_rows"] if row[0] == "负债利息")
    assert abs(float(interest_row[3]) - 126000.0) < 1e-6
    component_row = next(row for row in latest_report["production_details"] if row["item"] == "Components")
    product_row = next(row for row in latest_report["production_details"] if row["item"] == "Products")
    component_storage_row = next(row for row in latest_report["storage_summary"] if row["item"] == "Components")
    product_storage_row = next(row for row in latest_report["storage_summary"] if row["item"] == "Products")
    assert component_row["material_price"] == 258.0
    assert product_row["material_price"] == 630.0
    assert component_storage_row["unit_price"] == 24.0
    assert product_storage_row["unit_price"] == 100.0


def test_multiplayer_report_image_download_uses_room_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(app_module, "multiplayer_room_store", MultiplayerRoomStore(tmp_path / "multiplayer_rooms.json"))
    monkeypatch.setattr(app_module, "_render_report_image_bytes", lambda html: b"multiplayer-report-png")
    host = _login_client("Host3", "host3@example.com")
    guest = _login_client("Guest3", "guest3@example.com")

    create_response = host.post(
        "/multi/rooms",
        data={
            "_csrf": _fetch_csrf_token(host, "/multi/setup"),
            "seat_limit": "2",
            "bot_count": "0",
        },
        follow_redirects=False,
    )
    room_id = create_response.headers["location"].rsplit("/", 1)[-1]
    guest.post(
        f"/api/multi/rooms/{room_id}/join",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={},
    )
    host.post(
        f"/api/multi/rooms/{room_id}/ready",
        headers={"x-csrf-token": _fetch_csrf_token(host, "/")},
        json={"ready": True},
    )
    guest.post(
        f"/api/multi/rooms/{room_id}/ready",
        headers={"x-csrf-token": _fetch_csrf_token(guest, "/")},
        json={"ready": True},
    )
    host.post(
        f"/api/multi/rooms/{room_id}/start",
        headers={"x-csrf-token": _fetch_csrf_token(host, "/")},
        json={},
    )

    host_game = host.get(f"/multi/rooms/{room_id}/game")
    host_payload = _extract_round_payload(host_game.text)
    host.post(
        f"/multi/rooms/{room_id}/submit",
        data=_round_form_data(
            host_payload["initialPayload"],
            csrf_token=_fetch_csrf_token(host, f"/multi/rooms/{room_id}/game"),
            game_id=host_payload["gameId"],
        ),
        follow_redirects=False,
    )

    guest_game = guest.get(f"/multi/rooms/{room_id}/game")
    guest_payload = _extract_round_payload(guest_game.text)
    guest.post(
        f"/multi/rooms/{room_id}/submit",
        data=_round_form_data(
            guest_payload["initialPayload"],
            csrf_token=_fetch_csrf_token(guest, f"/multi/rooms/{room_id}/game"),
            game_id=guest_payload["gameId"],
        ),
        follow_redirects=False,
    )

    room = app_module.multiplayer_room_store.get_room_raw(room_id)
    assert room is not None
    guest_member = next(item for item in room["members"] if item["team_id"] == "17")
    latest_report = guest_member["latest_report_detail"]
    session = app_module._player_room_session(room, guest_member, "17")
    simulator = app_module._simulator("real-original")
    _html, cache_key = app_module._report_export_artifacts(simulator, session, latest_report)

    response = guest.get(f"/game/report-image/{cache_key}?game_id={session['game_id']}&download=1")

    assert response.status_code == 200
    assert response.content == b"multiplayer-report-png"
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == 'attachment; filename="report.png"'
