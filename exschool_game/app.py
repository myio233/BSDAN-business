from __future__ import annotations

import copy
from functools import lru_cache
from pathlib import Path
import logging
import secrets
import threading
from urllib.parse import quote
from uuid import uuid4
from datetime import datetime
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .auth_client import AuthClientError, AuthResult, AuthServiceUnavailableError, login_user, register_user, send_email_code
from .auth_store import auth_store
from .data_loader import describe_fixed_decision_source
from .engine import TEAM_ID, CampaignState, ExschoolSimulator, get_simulator
from .multiplayer_store import multiplayer_room_store
from .export_report_html import render_report_html
from .request_guard_service import request_guard_service
from .user_store import user_game_store


BASE_DIR = Path(__file__).resolve().parent
SCREENSHOT_SCRIPT = BASE_DIR / "scripts" / "screenshot_html.py"
REPORT_IMAGE_CACHE_DIR = BASE_DIR.parent / "storage" / "report_png_cache"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
SESSION_SECRET_STORAGE_PATH = BASE_DIR.parent / "storage" / "session_secret.txt"
APP_ROOT_PATH = os.environ.get("EXSCHOOL_ROOT_PATH", "").rstrip("/")
STATIC_ASSET_VERSION = str(
    int(
        max(
            path.stat().st_mtime
            for path in (BASE_DIR / "static").rglob("*")
            if path.is_file()
        )
    )
)
logger = logging.getLogger(__name__)


def _load_or_create_session_secret() -> str:
    configured_secret = os.environ.get("EXSCHOOL_SESSION_SECRET", "").strip()
    if configured_secret:
        return configured_secret
    SESSION_SECRET_STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing_secret = SESSION_SECRET_STORAGE_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        existing_secret = ""
    except OSError:
        existing_secret = ""
    if len(existing_secret) >= 32:
        return existing_secret
    generated_secret = hashlib.sha256(f"{uuid4().hex}:{uuid4().hex}".encode("utf-8")).hexdigest()
    temp_path = SESSION_SECRET_STORAGE_PATH.with_suffix(".tmp")
    try:
        temp_path.write_text(generated_secret, encoding="utf-8")
        temp_path.replace(SESSION_SECRET_STORAGE_PATH)
        return generated_secret
    except OSError:
        try:
            temp_path.unlink()
        except OSError:
            pass
        return generated_secret


SESSION_COOKIE_HTTPS_ONLY = os.environ.get("EXSCHOOL_SESSION_HTTPS_ONLY", "").strip().lower() in {"1", "true", "yes", "on"}
SESSION_SECRET = _load_or_create_session_secret()
app = FastAPI(title="Exschool 商赛模拟器", root_path=APP_ROOT_PATH)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    same_site="lax",
    https_only=SESSION_COOKIE_HTTPS_ONLY,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.middleware("http")
async def disable_html_cache(request: Request, call_next):
    response = await call_next(request)
    content_type = response.headers.get("content-type", "")
    if "text/html" in content_type:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response

GAME_DURATION_SECONDS = 40 * 60
ROUND_TIMEOUT_AUTO_SUBMIT_GRACE_SECONDS = 10
GAME_SUBMIT_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("GAME_SUBMIT_RATE_LIMIT_WINDOW_SECONDS", "60"))
GAME_SUBMIT_RATE_LIMIT_USER_ATTEMPTS = int(os.environ.get("GAME_SUBMIT_RATE_LIMIT_USER_ATTEMPTS", "20"))
GAME_SUBMIT_RATE_LIMIT_IP_ATTEMPTS = int(os.environ.get("GAME_SUBMIT_RATE_LIMIT_IP_ATTEMPTS", "60"))
REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS", "60"))
REPORT_IMAGE_RATE_LIMIT_USER_ATTEMPTS = int(os.environ.get("REPORT_IMAGE_RATE_LIMIT_USER_ATTEMPTS", "8"))
REPORT_IMAGE_RATE_LIMIT_IP_ATTEMPTS = int(os.environ.get("REPORT_IMAGE_RATE_LIMIT_IP_ATTEMPTS", "20"))
REPORT_IMAGE_CACHE_TTL_SECONDS = int(os.environ.get("REPORT_IMAGE_CACHE_TTL_SECONDS", str(7 * 24 * 60 * 60)))
REPORT_IMAGE_CACHE_MAX_FILES = int(os.environ.get("REPORT_IMAGE_CACHE_MAX_FILES", "256"))
HOME_CITY_OPTIONS = ["Shanghai", "Chengdu", "Wuhan", "Wuxi", "Ningbo"]
SINGLE_PLAYER_MODE_DEFAULT = "real-original"
SINGLE_PLAYER_MODE_LABELS = {
    "real-original": "真实原版竞争",
}
MULTIPLAYER_MODE_LABEL = "实时多人对战"
SINGLE_PLAYER_MODE_ALIASES = {
    "single": SINGLE_PLAYER_MODE_DEFAULT,
    "fixed": "real-original",
    "fixed-opponent": "real-original",
    "fixed_opponent": "real-original",
    "single-fixed": "real-original",
    "single-fixed-opponent": "real-original",
    "single_fixed_opponent": "real-original",
    "smart": "real-original",
    "high-intensity": "real-original",
    "high_intensity": "real-original",
    "practice": "real-original",
    "real": "real-original",
    "real-opponent": "real-original",
    "real_opponent": "real-original",
    "single-real": "real-original",
    "single-real-opponent": "real-original",
    "single_real_opponent": "real-original",
    "real-original": "real-original",
    "real_original": "real-original",
    "challenge": "real-original",
}
CITY_LABELS = {
    "Shanghai": "上海",
    "Chengdu": "成都",
    "Wuhan": "武汉",
    "Wuxi": "无锡",
    "Ningbo": "宁波",
}

SUMMARY_LABELS = {
    "Sales Revenue": "销售收入",
    "Net Assets": "净资产",
    "销售收入": "销售收入",
    "净资产": "净资产",
}
MULTIPLAYER_ROOM_POLL_INTERVAL_MS = 2_000


@lru_cache(maxsize=1)
def _multiplayer_team_order() -> list[str]:
    simulator = _simulator("real-original")
    summary_df = simulator.fixed_round_summary_df.copy()
    if summary_df.empty:
        return list(simulator.team_ids)
    final_round_id = simulator.available_rounds()[-1]
    final_rows = summary_df[summary_df["round_id"].astype(str) == final_round_id].copy()
    if final_rows.empty:
        return list(simulator.team_ids)
    final_rows["net_assets_est"] = (
        final_rows.get("ending_cash_est", 0.0).fillna(0.0)
        - final_rows.get("ending_debt_est", 0.0).fillna(0.0)
    )
    final_rows["team"] = final_rows["team"].astype(str)
    final_rows = final_rows.sort_values(["net_assets_est", "team"], ascending=[False, True])
    ordered = [str(value).strip() for value in final_rows["team"].tolist() if str(value).strip()]
    seen: set[str] = set()
    result: list[str] = []
    for team in ordered + list(simulator.team_ids):
        if team in seen:
            continue
        seen.add(team)
        result.append(team)
    return result


class GameFlowError(ValueError):
    pass


class RequestedGameNotFoundError(GameFlowError):
    pass


class CsrfError(ValueError):
    pass


def _normalize_single_player_mode(raw: object) -> str:
    normalized = str(raw or "").strip().lower()
    return SINGLE_PLAYER_MODE_ALIASES.get(normalized, SINGLE_PLAYER_MODE_DEFAULT)


def _single_player_mode_from_session(session: dict[str, object] | None) -> str:
    if not isinstance(session, dict):
        return SINGLE_PLAYER_MODE_DEFAULT
    return _normalize_single_player_mode(session.get("single_player_mode"))


@lru_cache(maxsize=8)
def _cached_single_player_mode_label(mode: str) -> str:
    normalized_mode = _normalize_single_player_mode(mode)
    base_label = SINGLE_PLAYER_MODE_LABELS.get(normalized_mode, SINGLE_PLAYER_MODE_LABELS[SINGLE_PLAYER_MODE_DEFAULT])
    if normalized_mode != "real-original":
        return base_label
    coverage = describe_fixed_decision_source(normalized_mode)
    coverage_ratio = str(coverage.get("coverage_ratio", "") or "").strip()
    expected_team_count = int(coverage.get("expected_team_count", 0) or 0)
    if expected_team_count <= 0 or not coverage_ratio or bool(coverage.get("coverage_complete")):
        return base_label
    return f"{base_label}（当前 {coverage_ratio} 队）"


def _single_player_mode_label(raw: object) -> str:
    return _cached_single_player_mode_label(_normalize_single_player_mode(raw))


def _round_clock_map(session: dict[str, object]) -> dict[str, int]:
    raw = session.get("round_started_at_ms_by_round")
    if not isinstance(raw, dict):
        raw = {}
        session["round_started_at_ms_by_round"] = raw
    normalized: dict[str, int] = {}
    for round_id, value in raw.items():
        try:
            normalized[str(round_id).strip().lower()] = int(value)
        except (TypeError, ValueError):
            continue
    if normalized != raw:
        session["round_started_at_ms_by_round"] = normalized
    return normalized


def _ensure_current_round_clock(session: dict[str, object]) -> bool:
    if not session.get("started"):
        return False
    current_round = str(session.get("current_round", "")).strip().lower()
    if not current_round:
        return False
    round_clock_map = _round_clock_map(session)
    if current_round in round_clock_map:
        return False
    round_clock_map[current_round] = int(time.time() * 1000)
    session["round_started_at_ms_by_round"] = round_clock_map
    return True


def _current_round_started_at_ms(session: dict[str, object]) -> int | None:
    current_round = str(session.get("current_round", "")).strip().lower()
    if not current_round:
        return None
    return _round_clock_map(session).get(current_round)


def _current_round_deadline_ms(session: dict[str, object]) -> int | None:
    started_at_ms = _current_round_started_at_ms(session)
    if started_at_ms is None:
        return None
    limit_seconds = int(session.get("time_limit_seconds", GAME_DURATION_SECONDS) or GAME_DURATION_SECONDS)
    return started_at_ms + limit_seconds * 1000


def _simulator(single_player_mode: str | None = None) -> ExschoolSimulator:
    return get_simulator(_normalize_single_player_mode(single_player_mode))


def _current_user(request: Request) -> dict[str, str] | None:
    raw = request.session.get("auth_user")
    if not isinstance(raw, dict):
        return None
    client_id = str(raw.get("client_id", "")).strip()
    auth_token = str(request.session.get("auth_token", "") or "").strip()
    if not client_id or not auth_token:
        _clear_auth_session(request)
        return None
    user = auth_store.get_public_by_session(client_id, auth_token)
    if user is None:
        _clear_auth_session(request)
        return None
    return user


def _save_auth_session(request: Request, result: AuthResult) -> None:
    request.session["auth_token"] = result.token
    request.session["auth_user"] = result.user.to_session()


def _clear_auth_session(request: Request) -> None:
    request.session.pop("auth_token", None)
    request.session.pop("auth_user", None)
    request.session.pop("game_session", None)
    request.session.pop("terminal_game_session", None)
    request.session.pop("selected_game_id", None)
    request.session.pop("pending_single_player_mode", None)


def _client_ip(request: Request) -> str:
    for header_name in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        raw_value = request.headers.get(header_name, "")
        if not raw_value:
            continue
        client_ip = raw_value.split(",", 1)[0].strip()
        if client_ip:
            return client_ip
    return request.client.host if request.client and request.client.host else ""


def _ensure_csrf_token(request: Request) -> str:
    raw = request.session.get("csrf_token")
    if isinstance(raw, str) and len(raw.strip()) >= 32:
        return raw.strip()
    token = secrets.token_urlsafe(32)
    request.session["csrf_token"] = token
    return token


def _require_csrf_token(request: Request, provided_token: object) -> None:
    expected = _ensure_csrf_token(request)
    actual = str(provided_token or "").strip()
    if not actual or not secrets.compare_digest(actual, expected):
        raise CsrfError("请求令牌无效，请刷新页面后重试。")


def _enforce_request_guard(
    *,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
    block_seconds: int,
    message: str,
) -> None:
    request_guard_service.enforce(
        scope=scope,
        identity=identity,
        limit=limit,
        window_seconds=window_seconds,
        block_seconds=block_seconds,
        message=message,
    )


def _submitted_round_ids(session: dict[str, object] | None) -> set[str]:
    if not isinstance(session, dict):
        return set()
    return {str(report.get("round_id", "")).strip().lower() for report in _reports_from_session(session.get("reports"))}


def _round_statuses(
    simulator: ExschoolSimulator,
    session: dict[str, object] | None,
    *,
    current_round: str | None = None,
) -> list[dict[str, str]]:
    submitted_round_ids = _submitted_round_ids(session)
    active_round = (current_round or str((session or {}).get("current_round", ""))).strip().lower()
    statuses: list[dict[str, str]] = []
    for round_id in simulator.available_rounds():
        normalized_round_id = str(round_id).strip().lower()
        if normalized_round_id in submitted_round_ids:
            status = "submitted"
            status_label = "已提交"
        elif normalized_round_id == active_round:
            status = "pending"
            status_label = "未提交"
        else:
            status = "upcoming"
            status_label = "未开始"
        statuses.append(
            {
                "round_id": normalized_round_id,
                "label": normalized_round_id.upper(),
                "status": status,
                "status_label": status_label,
            }
        )
    return statuses


def _active_game_summary_from_session(session: dict[str, object] | None) -> dict[str, str] | None:
    if not isinstance(session, dict):
        return None
    if not session.get("started") or session.get("archived") or session.get("terminal"):
        return None
    current_round = str(session.get("current_round", "")).upper()
    current_round_submitted = str(session.get("current_round", "")).strip().lower() in _submitted_round_ids(session)
    return {
        "game_id": str(session.get("game_id", "")).strip(),
        "company_name": str(session.get("company_name", "")),
        "current_round": current_round,
        "current_round_status_label": "已提交" if current_round_submitted else "未提交",
        "action_label": f"查看 {current_round} 财报" if current_round_submitted else f"继续 {current_round} 决策",
        "single_player_mode": _single_player_mode_from_session(session),
        "single_player_mode_label": _single_player_mode_label(_single_player_mode_from_session(session)),
    }


def _selected_game_id(request: Request) -> str | None:
    raw = str(request.session.get("selected_game_id", "") or "").strip()
    return raw or None


def _set_selected_game_id(request: Request, game_id: str | None) -> None:
    if game_id:
        request.session["selected_game_id"] = game_id
        return
    request.session.pop("selected_game_id", None)


def _terminal_game_session(request: Request) -> dict[str, object] | None:
    raw = request.session.get("terminal_game_session")
    return dict(raw) if isinstance(raw, dict) else None


def _set_terminal_game_session(request: Request, session: dict[str, object] | None) -> None:
    if isinstance(session, dict):
        request.session["terminal_game_session"] = dict(session)
        return
    request.session.pop("terminal_game_session", None)


def _active_game_saves(request: Request) -> list[dict[str, object]]:
    user = _current_user(request)
    if not user:
        return []
    selected_game_id = _selected_game_id(request)
    items = user_game_store.list_active_game_sessions(user["client_id"])
    normalized: list[dict[str, object]] = []
    for idx, item in enumerate(items):
        summary = _active_game_summary_from_session(item)
        if not summary:
            continue
        game_id = str(item.get("game_id", "")).strip()
        updated_at = float(item.get("updated_at", 0.0) or 0.0)
        normalized.append(
            {
                "game_id": game_id,
                "company_name": str(item.get("company_name", "")).strip() or "未命名公司",
                "home_city_label": CITY_LABELS.get(str(item.get("home_city", "")), str(item.get("home_city", "")) or "-"),
                "updated_at_label": datetime.fromtimestamp(updated_at).strftime("%Y-%m-%d %H:%M") if updated_at > 0 else "-",
                "is_selected": game_id == selected_game_id or (not selected_game_id and idx == 0),
                **summary,
            }
        )
    return normalized


def _recent_history(request: Request) -> list[dict[str, object]]:
    user = _current_user(request)
    if not user:
        return []
    items = user_game_store.list_history(user["client_id"])
    for item in items:
        completed_at = float(item.get("completed_at", 0.0) or 0.0)
        item["completed_at_label"] = (
            datetime.fromtimestamp(completed_at).strftime("%Y-%m-%d %H:%M") if completed_at > 0 else "-"
        )
        item["home_city_label"] = CITY_LABELS.get(str(item.get("home_city", "")), str(item.get("home_city", "")) or "-")
        item["single_player_mode_label"] = _single_player_mode_label(item.get("single_player_mode"))
        asset_accounting = item.get("asset_accounting")
        if not isinstance(asset_accounting, dict):
            asset_accounting = {}
        net_assets_label = str(asset_accounting.get("net_assets_label", "")).strip()
        item["final_net_assets_label"] = (
            str(asset_accounting.get("final_net_assets_label", "")).strip()
            or (f"最终{net_assets_label}" if net_assets_label else "最终净资产")
        )
    return items


def _room_is_full(room: dict[str, Any]) -> bool:
    players = _room_members(room)
    metadata = room.get("metadata") if isinstance(room.get("metadata"), dict) else {}
    human_seat_limit = int(
        room.get(
            "seat_limit",
            metadata.get("human_seat_limit", int(room.get("seat_count", 0) or 0) - int(room.get("bot_count", 0) or 0)),
        )
        or 0
    )
    return len(players) >= human_seat_limit


def _room_all_ready(room: dict[str, Any]) -> bool:
    players = _room_members(room)
    return bool(players) and all(bool(item.get("ready")) or bool(item.get("is_ready")) for item in players)


def _room_members(room: dict[str, Any]) -> list[dict[str, Any]]:
    raw = room.get("players")
    if not isinstance(raw, list):
        raw = room.get("members")
    members = [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []
    members.sort(
        key=lambda item: (
            float(item.get("joined_at", 0.0) or 0.0),
            int(item.get("seat_index", 0) or 0),
            str(item.get("client_id", "")),
        )
    )
    return members


def _room_player(room: dict[str, Any], client_id: str) -> dict[str, Any] | None:
    normalized_client_id = str(client_id).strip()
    for item in _room_members(room):
        if str(item.get("client_id", "")).strip() == normalized_client_id:
            return item
    return None


def _room_human_team_preview(room: dict[str, Any]) -> list[str]:
    team_order = _multiplayer_team_order()
    metadata = room.get("metadata") if isinstance(room.get("metadata"), dict) else {}
    seat_limit = int(
        room.get(
            "seat_limit",
            metadata.get("human_seat_limit", int(room.get("seat_count", 0) or 0) - int(room.get("bot_count", 0) or 0)),
        )
        or 0
    )
    return team_order[:seat_limit]


def _room_bot_team_preview(room: dict[str, Any]) -> list[str]:
    team_order = _multiplayer_team_order()
    metadata = room.get("metadata") if isinstance(room.get("metadata"), dict) else {}
    seat_limit = int(
        room.get(
            "seat_limit",
            metadata.get("human_seat_limit", int(room.get("seat_count", 0) or 0) - int(room.get("bot_count", 0) or 0)),
        )
        or 0
    )
    bot_count = int(room.get("bot_count", 0) or 0)
    return team_order[seat_limit : seat_limit + bot_count]


def _room_human_team_order(room: dict[str, Any]) -> list[str]:
    raw = room.get("human_team_order")
    if isinstance(raw, list) and raw:
        return [str(item).strip() for item in raw if str(item).strip()]
    return _room_human_team_preview(room)


def _room_bot_team_order(room: dict[str, Any]) -> list[str]:
    raw = room.get("bot_team_order")
    if isinstance(raw, list) and raw:
        return [str(item).strip() for item in raw if str(item).strip()]
    return _room_bot_team_preview(room)


def _room_active_team_ids(room: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for team_id in [*_room_human_team_order(room), *_room_bot_team_order(room)]:
        normalized = str(team_id).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _room_home_city_for_team(room: dict[str, Any], team_id: str) -> str:
    simulator = _simulator("real-original")
    normalized_team_id = str(team_id).strip()
    assignment = next((item for item in _room_assignments(room) if item["team_id"] == normalized_team_id), None)
    if assignment is not None:
        selected_home_city = str(assignment.get("home_city", "") or "").strip()
        if selected_home_city in HOME_CITY_OPTIONS:
            return selected_home_city
    return simulator.team_home_city_map.get(normalized_team_id, HOME_CITY_OPTIONS[0])


def _room_assignments(room: dict[str, Any]) -> list[dict[str, str]]:
    players = _room_members(room)
    ordered_teams = _room_human_team_order(room)
    assignments: list[dict[str, str]] = []
    simulator = _simulator("real-original")
    for idx, player in enumerate(players):
        if idx >= len(ordered_teams):
            break
        team_id = ordered_teams[idx]
        assignments.append(
            {
                "client_id": str(player.get("client_id", "")).strip(),
                "name": str(player.get("name", "")).strip(),
                "team_id": team_id,
                "home_city": (
                    str(player.get("home_city", "")).strip()
                    if str(player.get("home_city", "")).strip() in HOME_CITY_OPTIONS
                    else simulator.team_home_city_map.get(team_id, HOME_CITY_OPTIONS[0])
                ),
            }
        )
    return assignments


def _serialize_team_states(states: dict[str, CampaignState | None]) -> dict[str, object]:
    return {
        str(team): _state_to_session(state)
        for team, state in states.items()
    }


def _deserialize_team_states(raw: object) -> dict[str, CampaignState | None]:
    if not isinstance(raw, dict):
        return {}
    return {
        str(team): _state_from_session(state)
        for team, state in raw.items()
    }


def _player_room_session(room: dict[str, Any], player: dict[str, Any], team_id: str) -> dict[str, object]:
    home_city = _room_home_city_for_team(room, team_id)
    team_state = _room_team_states(room).get(team_id)
    return {
        "company_name": f"{str(player.get('name', '')).strip() or '玩家'} · C{team_id}",
        "home_city": home_city,
        "single_player_mode": "real-original",
        "current_round": str(room.get("current_round", "r1")).strip().lower(),
        "started": str(room.get("status", "lobby")).strip().lower() in {"active", "report", "finished"},
        "time_limit_seconds": int(room.get("time_limit_seconds", GAME_DURATION_SECONDS) or GAME_DURATION_SECONDS),
        "game_id": f"{room['room_id']}:{team_id}",
        "campaign_state": _state_to_session(team_state),
        "reports": list(player.get("reports", [])) if isinstance(player.get("reports"), list) else [],
        "report_details": {},
        "latest_report_detail": dict(player.get("latest_report_detail", {})) if isinstance(player.get("latest_report_detail"), dict) else None,
        "report_image_cache_keys": {},
        "all_company_rounds": list(player.get("all_company_rounds", [])) if isinstance(player.get("all_company_rounds"), list) else [],
        "round_started_at_ms_by_round": dict(room.get("round_started_at_ms_by_round", {})) if isinstance(room.get("round_started_at_ms_by_round"), dict) else {},
        "archived": False,
        "terminal": str(room.get("status", "")).strip().lower() == "finished",
        "multiplayer_room_id": str(room.get("room_id", "")),
        "multiplayer_team_id": team_id,
    }


def _multiplayer_report_session_for_game_id(
    requested_game_id: str | None,
    user: dict[str, str] | None,
) -> tuple[dict[str, object], dict[str, object], Simulator] | None:
    if user is None:
        return None
    normalized_game_id = str(requested_game_id or "").strip()
    if ":" not in normalized_game_id:
        return None
    room_id, requested_team_id = normalized_game_id.split(":", 1)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return None
    assignment = next((item for item in _room_assignments(room) if item["client_id"] == user["client_id"]), None)
    player = _room_player(room, user["client_id"])
    if assignment is None or not isinstance(player, dict):
        return None
    assigned_team_id = str(assignment.get("team_id", "")).strip()
    if not assigned_team_id or assigned_team_id != str(requested_team_id).strip():
        return None
    latest_report = player.get("latest_report_detail")
    if not isinstance(latest_report, dict):
        return None
    session = _player_room_session(room, player, assigned_team_id)
    return session, latest_report, _simulator("real-original")


def _room_snapshot(room: dict[str, Any], *, current_client_id: str | None = None) -> dict[str, Any]:
    simulator = _simulator("real-original")
    assignments = _room_assignments(room)
    assignment_map = {item["client_id"]: item for item in assignments}
    current_round = str(room.get("current_round", "r1")).strip().lower()
    pending_by_round = room.get("pending_submissions", {})
    current_round_submissions = dict(pending_by_round.get(current_round, {})) if isinstance(pending_by_round, dict) else {}
    seat_entries: list[dict[str, Any]] = []
    players_snapshot: list[dict[str, Any]] = []
    for item in _room_members(room):
        client_id = str(item.get("client_id", "")).strip()
        assignment = assignment_map.get(client_id, {})
        team_id = str(assignment.get("team_id", item.get("team_id") or "")).strip()
        players_snapshot.append(
            {
                "client_id": client_id,
                "name": str(item.get("name", "")).strip(),
                "ready": bool(item.get("ready")) or bool(item.get("is_ready")),
                "team_id": team_id,
                "team_label": f"C{team_id}" if team_id else "待分配",
                "home_city_label": CITY_LABELS.get(str(assignment.get("home_city", "")), str(assignment.get("home_city", ""))),
                "submitted_current_round": bool(team_id and team_id in current_round_submissions),
                "is_current_user": bool(current_client_id and client_id == current_client_id),
                "latest_report_round": (
                    str(item.get("latest_report_detail", {}).get("round_id", "")).strip().lower()
                    if isinstance(item.get("latest_report_detail"), dict)
                    else ""
                ),
            }
        )
        seat_entries.append(
            {
                "seat_id": team_id or client_id or str(len(seat_entries) + 1),
                "label": f"席位 {len(seat_entries) + 1}",
                "occupied": True,
                "ready": bool(item.get("ready")) or bool(item.get("is_ready")),
                "is_host": client_id == str(room.get("host_client_id", "")).strip(),
                "current_user_here": bool(current_client_id and current_client_id == client_id),
                "submitted_current_round": bool(team_id and team_id in current_round_submissions),
                "player": {
                    "id": client_id,
                    "name": str(item.get("name", "")).strip(),
                    "email": str(item.get("email", "")).strip().lower(),
                    "ready": bool(item.get("ready")) or bool(item.get("is_ready")),
                },
                "team_id": team_id,
                "team_label": f"C{team_id}" if team_id else "待分配",
            }
        )
    bot_snapshot = [
        {
            "team_id": team_id,
            "team_label": f"C{team_id}",
            "home_city_label": CITY_LABELS.get(simulator.team_home_city_map.get(team_id, ""), simulator.team_home_city_map.get(team_id, "")),
            "submitted_current_round": bool(str(room.get("status", "")).strip().lower() != "lobby"),
            "is_bot": True,
        }
        for team_id in _room_bot_team_order(room)
    ]
    for bot in bot_snapshot:
        seat_entries.append(
            {
                "seat_id": f"bot-{bot['team_id']}",
                "label": f"Bot · C{bot['team_id']}",
                "occupied": True,
                "ready": True,
                "is_bot": True,
                "player": {
                    "name": f"Bot C{bot['team_id']}",
                    "ready": True,
                },
                "team_id": bot["team_id"],
                "team_label": bot["team_label"],
            }
        )
    metadata = room.get("metadata") if isinstance(room.get("metadata"), dict) else {}
    seat_limit = int(
        room.get(
            "seat_limit",
            metadata.get("human_seat_limit", int(room.get("seat_count", 0) or 0) - int(room.get("bot_count", 0) or 0)),
        )
        or 0
    )
    for index in range(len(seat_entries), max(seat_limit + len(bot_snapshot), len(seat_entries))):
        seat_entries.append(
            {
                "seat_id": f"open-{index + 1}",
                "label": f"席位 {index + 1}",
                "occupied": False,
                "ready": False,
                "player": {},
            }
        )
    round_deadline_ms = None
    round_started_map = room.get("round_started_at_ms_by_round", {})
    if isinstance(round_started_map, dict):
        round_started_ms = round_started_map.get(current_round)
        if round_started_ms is not None:
            round_deadline_ms = int(round_started_ms) + int(room.get("time_limit_seconds", GAME_DURATION_SECONDS) or GAME_DURATION_SECONDS) * 1000
    current_player_assignment = assignment_map.get(str(current_client_id or "").strip(), {})
    current_player_data = _room_player(room, str(current_client_id or "").strip()) if current_client_id else None
    current_player_ready = (
        (bool(current_player_data.get("ready")) or bool(current_player_data.get("is_ready")))
        if isinstance(current_player_data, dict)
        else False
    )
    current_player_team_id = str(current_player_assignment.get("team_id", "")).strip()
    current_player_submitted = bool(current_player_team_id and current_player_team_id in current_round_submissions)
    host_client_id = str(room.get("host_client_id", "")).strip()
    room_code = str(room.get("room_code", "")).strip().upper() or str(room.get("room_id", "")).strip().upper()
    room_name = str(room.get("room_name", "")).strip() or f"多人房间 {room_code}"
    return {
        "room_id": str(room.get("room_id", "")),
        "room_code": room_code,
        "room_name": room_name,
        "status": str(room.get("status", "lobby")).strip().lower(),
        "status_label": (
            "等待中"
            if str(room.get("status", "lobby")).strip().lower() == "lobby"
            else ("查看财报" if str(room.get("status", "")).strip().lower() == "report" else ("已结束" if str(room.get("status", "")).strip().lower() == "finished" else "进行中"))
        ),
        "seat_limit": seat_limit,
        "max_seats": seat_limit + int(room.get("bot_count", 0) or 0),
        "bot_count": int(room.get("bot_count", 0) or 0),
        "is_full": _room_is_full(room),
        "all_ready": _room_all_ready(room),
        "current_round": current_round,
        "current_round_label": current_round.upper(),
        "time_limit_seconds": int(room.get("time_limit_seconds", GAME_DURATION_SECONDS) or GAME_DURATION_SECONDS),
        "poll_interval_ms": MULTIPLAYER_ROOM_POLL_INTERVAL_MS,
        "room_started_at_ms": round_started_map.get(current_round) if isinstance(round_started_map, dict) else None,
        "room_deadline_ms": round_deadline_ms,
        "updated_at": datetime.fromtimestamp(float(room.get("updated_at", time.time()) or time.time())).isoformat(),
        "host": {
            "client_id": host_client_id,
            "name": (
                str(_room_player(room, host_client_id).get("name", "")).strip()
                if isinstance(_room_player(room, host_client_id), dict)
                else ""
            ),
        },
        "players": players_snapshot,
        "seats": seat_entries,
        "bots": bot_snapshot,
        "current_player": {
            "name": str(current_player_data.get("name", "")).strip() if isinstance(current_player_data, dict) else "",
            "in_room": bool(current_player_data),
            "ready": current_player_ready,
            "is_host": bool(current_client_id and current_client_id == host_client_id),
            "submitted_current_round": current_player_submitted,
            "seat_label": f"C{current_player_assignment.get('team_id')}" if current_player_assignment.get("team_id") else "未就座",
            "home_city": str(current_player_assignment.get("home_city", "")).strip(),
            "home_city_label": CITY_LABELS.get(
                str(current_player_assignment.get("home_city", "")).strip(),
                str(current_player_assignment.get("home_city", "")).strip(),
            ),
        },
        "permissions": {
            "can_leave": bool(current_player_data) and str(room.get("status", "lobby")).strip().lower() == "lobby",
            "can_toggle_ready": bool(current_player_data) and str(room.get("status", "lobby")).strip().lower() == "lobby",
            "can_start": bool(current_client_id and current_client_id == host_client_id) and str(room.get("status", "lobby")).strip().lower() == "lobby" and _room_is_full(room) and _room_all_ready(room),
            "can_choose_seat": str(room.get("status", "lobby")).strip().lower() == "lobby",
            "can_manage_bots": bool(current_client_id and current_client_id == host_client_id) and str(room.get("status", "lobby")).strip().lower() == "lobby",
            "can_update_home_city": bool(current_player_data) and str(room.get("status", "lobby")).strip().lower() == "lobby",
        },
        "can_start": str(room.get("status", "lobby")).strip().lower() == "lobby" and _room_is_full(room) and _room_all_ready(room),
        "latest_round_id": room.get("latest_round_id"),
    }


def _active_multiplayer_rooms(request: Request) -> list[dict[str, object]]:
    user = _current_user(request)
    if not user:
        return []
    rooms = multiplayer_room_store.list_rooms_for_user(user["client_id"])
    normalized: list[dict[str, object]] = []
    for room in rooms:
        snapshot = _room_snapshot(room, current_client_id=user["client_id"])
        normalized.append(
            {
                "room_id": snapshot["room_id"],
                "room_code": snapshot["room_code"],
                "status_label": "已开始" if snapshot["status"] == "active" else ("已结束" if snapshot["status"] == "finished" else "等待中"),
                "seat_label": f"{len(snapshot['players'])}/{snapshot['seat_limit']}",
                "bot_label": f"{snapshot['bot_count']} 个 bot",
                "current_round_label": snapshot["current_round_label"],
            }
        )
    return normalized


def _room_player_submission_payload(
    simulator: ExschoolSimulator,
    context: dict[str, Any],
    form_payload: dict[str, Any],
) -> dict[str, Any]:
    round_id = str(context.get("round_id", form_payload.get("round_id", "")) or "")
    payload = simulator._payload_for_context(round_id, context)
    payload["loan_delta"] = float(form_payload.get("loan_delta", payload["loan_delta"]) or 0.0)
    payload["workers"] = int(form_payload.get("workers", payload["workers"]) or 0)
    payload["engineers"] = int(form_payload.get("engineers", payload["engineers"]) or 0)
    payload["worker_salary"] = float(form_payload.get("worker_salary", payload["worker_salary"]) or 0.0)
    payload["engineer_salary"] = float(form_payload.get("engineer_salary", payload["engineer_salary"]) or 0.0)
    payload["management_investment"] = float(form_payload.get("management_investment", payload["management_investment"]) or 0.0)
    payload["quality_investment"] = float(form_payload.get("quality_investment", payload["quality_investment"]) or 0.0)
    payload["research_investment"] = float(form_payload.get("research_investment", payload["research_investment"]) or 0.0)
    payload["products_planned"] = int(form_payload.get("products_planned", payload["products_planned"]) or 0)
    for market in context["visible_markets"]:
        slug = market.lower()
        payload["markets"][market] = {
            "agent_change": int(form_payload.get(f"{slug}_agent_change", payload["markets"][market]["agent_change"]) or 0),
            "marketing_investment": float(
                form_payload.get(f"{slug}_marketing_investment", payload["markets"][market]["marketing_investment"]) or 0.0
            ),
            "price": float(form_payload.get(f"{slug}_price", payload["markets"][market]["price"]) or 0.0),
            "subscribed_market_report": form_payload.get(f"{slug}_market_report") == "1",
        }
    return payload


def _room_team_states(room: dict[str, Any]) -> dict[str, CampaignState | None]:
    simulator = _simulator("real-original")
    states = _deserialize_team_states(room.get("team_states"))
    active_team_ids = set(_room_active_team_ids(room))
    resolved: dict[str, CampaignState | None] = {}
    for team in simulator.team_ids:
        state = states.get(team)
        if state is None and team in active_team_ids:
            state = simulator._initial_company_state(
                team,
                _room_home_city_for_team(room, team),
                preserve_real_original_round1=False,
            )
        resolved[team] = state
    return resolved


def _initial_room_team_states(room: dict[str, Any]) -> dict[str, object]:
    simulator = _simulator("real-original")
    return _serialize_team_states(
        {
            team_id: simulator._initial_company_state(
                team_id,
                _room_home_city_for_team(room, team_id),
                preserve_real_original_round1=False,
            )
            for team_id in _room_active_team_ids(room)
        }
    )


def _room_team_context(simulator: ExschoolSimulator, room: dict[str, Any], team_id: str) -> dict[str, Any]:
    states = _room_team_states(room)
    round_id = str(room.get("current_round", "r1")).strip().lower()
    return simulator._context_for_company_state(
        round_id,
        team_id,
        states.get(team_id),
        current_home_city=_room_home_city_for_team(room, team_id),
        game_id=str(room.get("room_id", "")),
    )


def _settle_room_round(room: dict[str, Any]) -> dict[str, Any]:
    simulator = _simulator("real-original")
    round_id = str(room.get("current_round", "r1")).strip().lower()
    assignments = _room_assignments(room)
    human_team_ids = [item["team_id"] for item in assignments]
    participant_team_ids = _room_active_team_ids(room)
    home_city_by_team = {item["team_id"]: _room_home_city_for_team(room, item["team_id"]) for item in assignments}
    states = _room_team_states(room)
    pending_submissions = dict(room.get("pending_submissions", {})) if isinstance(room.get("pending_submissions"), dict) else {}
    current_round_submissions = dict(pending_submissions.get(round_id, {})) if isinstance(pending_submissions.get(round_id, {}), dict) else {}
    human_decisions_by_team: dict[str, Any] = {}
    for assignment in assignments:
        team_id = assignment["team_id"]
        team_context = simulator._context_for_company_state(
            round_id,
            team_id,
            states.get(team_id),
            current_home_city=_room_home_city_for_team(room, team_id),
            game_id=str(room.get("room_id", "")),
        )
        raw_payload = current_round_submissions.get(team_id) or simulator._payload_for_context(round_id, team_context)
        human_decisions_by_team[team_id] = simulator._build_simulation_input(
            round_id,
            raw_payload,
            context=team_context,
            headcount_is_delta=True,
        )
    reports_by_team, next_states, _all_company_results = simulator.simulate_room_round(
        round_id=round_id,
        human_decisions_by_team=human_decisions_by_team,
        human_team_ids=human_team_ids,
        team_states=states,
        game_id=str(room.get("room_id", "")),
        mode="multiplayer",
        participant_team_ids=participant_team_ids,
        current_home_city_by_team=home_city_by_team,
        use_historical_initial_state=False,
    )
    players = []
    for player in _room_members(room):
        updated = dict(player)
        assignment = next((item for item in assignments if item["client_id"] == str(player.get("client_id", "")).strip()), None)
        if assignment:
            team_id = assignment["team_id"]
            latest_report = reports_by_team[team_id]
            reports = _reports_from_session(updated.get("reports"))
            reports = [item for item in reports if str(item.get("round_id", "")).strip().lower() != round_id]
            reports.append(_report_summary(latest_report))
            updated["reports"] = _reports_to_session(reports)
            updated["latest_report_detail"] = latest_report
            all_company_rounds = _all_company_rounds_from_session(updated.get("all_company_rounds"))
            all_company_rounds = [item for item in all_company_rounds if str(item.get("round_id", "")).strip().lower() != round_id]
            all_company_rounds.append(_compact_all_company_round_point(latest_report))
            updated["all_company_rounds"] = _all_company_rounds_to_session(all_company_rounds)
            submitted_round_ids = {str(item).strip().lower() for item in updated.get("submitted_round_ids", []) if str(item).strip()}
            submitted_round_ids.add(round_id)
            updated["submitted_round_ids"] = sorted(submitted_round_ids, key=str)
            updated["team_id"] = team_id
            updated["ready"] = False
            updated["is_ready"] = False
            updated["updated_at"] = time.time()
        players.append(updated)
    next_round_id = _next_round(simulator, round_id)
    updated_room = dict(room)
    updated_room["members"] = players
    updated_room["team_states"] = _serialize_team_states(next_states)
    updated_room["latest_reports_by_team"] = reports_by_team
    updated_room["latest_round_id"] = round_id
    updated_room["pending_submissions"] = {}
    if next_round_id is None:
        updated_room["status"] = "finished"
    else:
        updated_room["status"] = "report"
    return updated_room


def _ensure_room_progress(room: dict[str, Any]) -> dict[str, Any]:
    if str(room.get("status", "")).strip().lower() != "active":
        return room
    round_id = str(room.get("current_round", "r1")).strip().lower()
    assignments = _room_assignments(room)
    human_team_ids = [item["team_id"] for item in assignments]
    pending_submissions = dict(room.get("pending_submissions", {})) if isinstance(room.get("pending_submissions"), dict) else {}
    current_round_submissions = dict(pending_submissions.get(round_id, {})) if isinstance(pending_submissions.get(round_id, {}), dict) else {}
    if all(team_id in current_round_submissions for team_id in human_team_ids):
        settled = _settle_room_round(room)
        return multiplayer_room_store.save_room(settled)
    round_started_map = room.get("round_started_at_ms_by_round", {})
    if not isinstance(round_started_map, dict):
        return room
    started_at_ms = round_started_map.get(round_id)
    if started_at_ms is None:
        return room
    deadline_ms = int(started_at_ms) + int(room.get("time_limit_seconds", GAME_DURATION_SECONDS) or GAME_DURATION_SECONDS) * 1000
    current_ms = int(time.time() * 1000)
    if current_ms <= deadline_ms + ROUND_TIMEOUT_AUTO_SUBMIT_GRACE_SECONDS * 1000:
        return room
    simulator = _simulator("real-original")
    updated_room = dict(room)
    updated_pending = dict(pending_submissions)
    updated_current_round = dict(current_round_submissions)
    for assignment in assignments:
        team_id = assignment["team_id"]
        if team_id in updated_current_round:
            continue
        team_context = _room_team_context(simulator, room, team_id)
        updated_current_round[team_id] = simulator._payload_for_context(round_id, team_context)
    updated_pending[round_id] = updated_current_round
    updated_room["pending_submissions"] = updated_pending
    settled = _settle_room_round(updated_room)
    return multiplayer_room_store.save_room(settled)


def _room_json_response(room: dict[str, Any], *, current_client_id: str | None = None, message: str | None = None) -> JSONResponse:
    payload = _room_snapshot(room, current_client_id=current_client_id)
    if message:
        payload["message"] = message
    return JSONResponse(payload)


def _build_common_context(request: Request) -> dict[str, object]:
    user = _current_user(request)
    active_game_saves: list[dict[str, object]] = []
    active_multiplayer_rooms: list[dict[str, object]] = []
    if user:
        selected_game_id = _selected_game_id(request)
        session = user_game_store.get_active_game_session(user["client_id"], game_id=selected_game_id) or user_game_store.get_active_game_session(
            user["client_id"]
        )
        active_game_saves = _active_game_saves(request)
        active_multiplayer_rooms = _active_multiplayer_rooms(request)
        request.session.pop("game_session", None)
    else:
        session = request.session.get("game_session")
    recent_history = _recent_history(request)
    return {
        "current_user": user,
        "active_game_summary": _active_game_summary_from_session(session if isinstance(session, dict) else None),
        "active_game_saves": active_game_saves,
        "active_multiplayer_rooms": active_multiplayer_rooms,
        "recent_history": recent_history,
        "recent_history_final_net_assets_label": (
            str(recent_history[0].get("final_net_assets_label", "最终净资产")) if recent_history else "最终净资产"
        ),
        "static_asset_version": STATIC_ASSET_VERSION,
        "csrf_token": _ensure_csrf_token(request),
    }


def _template_response(
    request: Request,
    template_name: str,
    context: dict[str, object] | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    merged = _build_common_context(request)
    if context:
        merged.update(context)
    return templates.TemplateResponse(request, template_name, merged, status_code=status_code)


def _auth_redirect(request: Request) -> RedirectResponse:
    return RedirectResponse(url=str(request.url_for("auth_page")), status_code=303)


def _default_session(simulator: ExschoolSimulator) -> dict[str, object]:
    return {
        "company_name": "",
        "home_city": HOME_CITY_OPTIONS[0],
        "single_player_mode": SINGLE_PLAYER_MODE_DEFAULT,
        "current_round": simulator.available_rounds()[0],
        "started": False,
        "time_limit_seconds": GAME_DURATION_SECONDS,
        "game_id": uuid4().hex,
        "campaign_state": None,
        "reports": [],
        "report_details": {},
        "latest_report_detail": None,
        "report_image_cache_keys": {},
        "all_company_rounds": [],
        "round_started_at_ms_by_round": {},
        "archived": False,
        "terminal": False,
    }


def _normalize_game_id(raw: object) -> str | None:
    value = str(raw or "").strip()
    return value or None


def _preserve_numeric_form_value(value: object, *, integer: bool = False) -> int | float | str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return int(raw) if integer else float(raw)
    except (TypeError, ValueError):
        return raw


def _get_game_session(
    request: Request,
    simulator: ExschoolSimulator,
    *,
    requested_game_id: str | None = None,
    persist_selected_game_id: bool = True,
) -> dict[str, object]:
    user = _current_user(request)
    if user:
        request.session.pop("game_session", None)
        explicit_game_id = _normalize_game_id(requested_game_id)
        if explicit_game_id:
            session = user_game_store.get_active_game_session(user["client_id"], game_id=explicit_game_id)
            if session is None:
                raise RequestedGameNotFoundError("未找到指定对局。")
        else:
            selected_game_id = _selected_game_id(request)
            session = user_game_store.get_active_game_session(user["client_id"], game_id=selected_game_id) or user_game_store.get_active_game_session(
                user["client_id"]
            )
        if session is None:
            return _default_session(simulator)
        if persist_selected_game_id:
            _set_selected_game_id(request, str(session.get("game_id", "")).strip() or None)
        if _ensure_current_round_clock(session):
            user_game_store.save_active_game_session(user, session)
        return session
    session = request.session.get("game_session")
    if not isinstance(session, dict):
        session = _default_session(simulator)
        request.session["game_session"] = session
    elif _ensure_current_round_clock(session):
        request.session["game_session"] = session
    return session


def _save_game_session(request: Request, session: dict[str, object]) -> None:
    _ensure_current_round_clock(session)
    user = _current_user(request)
    if not user:
        if session.get("terminal"):
            _set_terminal_game_session(request, session)
            request.session.pop("game_session", None)
            return
        request.session["game_session"] = session
        return
    request.session.pop("game_session", None)
    game_id = str(session.get("game_id", "")).strip() or None
    if session.get("terminal"):
        _set_terminal_game_session(request, session)
    if session.get("archived"):
        if game_id:
            user_game_store.clear_active_game_session(user["client_id"], game_id=game_id)
        fallback_session = user_game_store.get_active_game_session(user["client_id"])
        _set_selected_game_id(request, str(fallback_session.get("game_id", "")).strip() if fallback_session else None)
        return
    if session.get("started"):
        user_game_store.save_active_game_session(user, session)
        _set_selected_game_id(request, game_id)
        return
    if game_id and user_game_store.get_active_game_session(user["client_id"], game_id=game_id):
        _set_selected_game_id(request, game_id)


def _report_summary(report: dict[str, object]) -> dict[str, object]:
    key_metrics = dict(report.get("key_metrics", {}))
    localized_metrics = {
        SUMMARY_LABELS.get(str(label), str(label)): float(value or 0.0)
        for label, value in key_metrics.items()
        if str(label) in SUMMARY_LABELS
    }
    total_assets = float(report.get("total_assets", key_metrics.get("总资产", 0.0)) or 0.0)
    net_assets = float(report.get("net_assets", key_metrics.get("净资产", 0.0)) or 0.0)
    asset_accounting = report.get("asset_accounting")
    return {
        "round_id": str(report["round_id"]),
        "key_metrics": localized_metrics,
        "total_assets": total_assets,
        "net_assets": net_assets,
        "net_profit": float(report.get("net_profit", 0.0) or 0.0),
        "ending_cash": float(report.get("ending_cash", 0.0) or 0.0),
        "ending_debt": float(report.get("ending_debt", 0.0) or 0.0),
        "asset_accounting": dict(asset_accounting) if isinstance(asset_accounting, dict) else {},
    }


def _equation_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _equation_rows(key_data: dict[str, object]) -> list[dict[str, str]]:
    rows = key_data.get("equations_rows")
    if isinstance(rows, list):
        normalized: list[dict[str, str]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = str(row.get("item", "")).strip()
            formula = str(row.get("formula", "")).strip()
            if item:
                normalized.append({"item": item, "formula": formula})
        if normalized:
            return normalized
    return [{"item": "", "formula": line} for line in _equation_lines(str(key_data.get("equations_text", "")))]


def _reports_to_session(reports: list[dict[str, object]]) -> list[list[object]]:
    compact: list[list[object]] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        key_metrics = dict(report.get("key_metrics", {}))
        compact.append(
            [
                str(report["round_id"]),
                float(report.get("total_assets", 0.0) or 0.0),
                float(report.get("net_assets", 0.0) or 0.0),
                float(report.get("net_profit", 0.0) or 0.0),
                float(report.get("ending_cash", 0.0) or 0.0),
                float(report.get("ending_debt", 0.0) or 0.0),
                float(key_metrics.get("销售收入", 0.0) or 0.0),
                dict(report.get("asset_accounting", {})) if isinstance(report.get("asset_accounting"), dict) else {},
            ]
        )
    return compact


def _reports_from_session(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    reports: list[dict[str, object]] = []
    for item in raw:
        if item is None:
            continue
        if isinstance(item, dict):
            reports.append(item)
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 7:
            reports.append(
                {
                    "round_id": str(item[0]),
                    "key_metrics": {"销售收入": float(item[6] or 0.0), "净资产": float(item[2] or 0.0)},
                    "total_assets": float(item[1] or 0.0),
                    "net_assets": float(item[2] or 0.0),
                    "net_profit": float(item[3] or 0.0),
                    "ending_cash": float(item[4] or 0.0),
                    "ending_debt": float(item[5] or 0.0),
                    "asset_accounting": dict(item[7]) if len(item) >= 8 and isinstance(item[7], dict) else {},
                }
            )
    return reports


def _report_image_cache_keys_from_session(session: dict[str, object] | None) -> dict[str, str]:
    if not isinstance(session, dict):
        return {}
    raw = session.get("report_image_cache_keys")
    normalized: dict[str, str] = {}
    if isinstance(raw, dict):
        for round_id, cache_key in raw.items():
            normalized_round_id = str(round_id or "").strip().lower()
            normalized_cache_key = str(cache_key or "").strip().lower()
            if (
                normalized_round_id
                and len(normalized_cache_key) == 64
                and all(ch in "0123456789abcdef" for ch in normalized_cache_key)
            ):
                normalized[normalized_round_id] = normalized_cache_key
    if raw != normalized:
        session["report_image_cache_keys"] = normalized
    return normalized


def _remember_report_image_cache_key(session: dict[str, object], *, round_id: object, cache_key: str) -> None:
    normalized_round_id = str(round_id or "").strip().lower()
    normalized_cache_key = str(cache_key or "").strip().lower()
    if not normalized_round_id or len(normalized_cache_key) != 64 or any(ch not in "0123456789abcdef" for ch in normalized_cache_key):
        return
    cache_keys = _report_image_cache_keys_from_session(session)
    if cache_keys.get(normalized_round_id) == normalized_cache_key:
        return
    cache_keys[normalized_round_id] = normalized_cache_key
    session["report_image_cache_keys"] = cache_keys


def _report_image_cache_key_belongs_to_session(session: dict[str, object] | None, cache_key: str) -> bool:
    normalized_cache_key = str(cache_key or "").strip().lower()
    if len(normalized_cache_key) != 64 or any(ch not in "0123456789abcdef" for ch in normalized_cache_key):
        return False
    return normalized_cache_key in _report_image_cache_keys_from_session(session).values()


def _latest_report_detail_from_session(session: dict[str, object] | None) -> dict[str, object] | None:
    if not isinstance(session, dict):
        return None
    raw = session.get("latest_report_detail")
    return dict(raw) if isinstance(raw, dict) else None


def _report_details_from_session(session: dict[str, object] | None) -> dict[str, dict[str, object]]:
    if not isinstance(session, dict):
        return {}
    raw = session.get("report_details")
    normalized: dict[str, dict[str, object]] = {}
    if isinstance(raw, dict):
        for round_id, report in raw.items():
            normalized_round_id = str(round_id or "").strip().lower()
            if normalized_round_id and isinstance(report, dict):
                normalized[normalized_round_id] = copy.deepcopy(report)
    latest_report = _latest_report_detail_from_session(session)
    if latest_report is not None:
        latest_round_id = str(latest_report.get("round_id", "")).strip().lower()
        if latest_round_id:
            normalized[latest_round_id] = copy.deepcopy(latest_report)
    if raw != normalized:
        session["report_details"] = normalized
    return normalized


def _remember_report_detail(session: dict[str, object], report: dict[str, object]) -> None:
    normalized_round_id = str(report.get("round_id", "")).strip().lower()
    if not normalized_round_id:
        return
    report_details = _report_details_from_session(session)
    report_details[normalized_round_id] = copy.deepcopy(report)
    session["report_details"] = report_details


def _report_detail_for_round_id(session: dict[str, object] | None, round_id: object) -> dict[str, object] | None:
    normalized_round_id = str(round_id or "").strip().lower()
    if not normalized_round_id:
        return None
    report = _report_details_from_session(session).get(normalized_round_id)
    return copy.deepcopy(report) if isinstance(report, dict) else None


def _report_detail_for_cache_key(session: dict[str, object] | None, cache_key: str) -> dict[str, object] | None:
    normalized_cache_key = str(cache_key or "").strip().lower()
    if len(normalized_cache_key) != 64 or any(ch not in "0123456789abcdef" for ch in normalized_cache_key):
        return None
    for round_id, remembered_cache_key in _report_image_cache_keys_from_session(session).items():
        if remembered_cache_key == normalized_cache_key:
            return _report_detail_for_round_id(session, round_id)
    return None


def _state_from_session(raw: object) -> CampaignState | None:
    if not isinstance(raw, dict):
        return None
    return CampaignState(
        current_cash=float(raw["current_cash"]),
        current_debt=float(raw["current_debt"]),
        workers=int(raw["workers"]),
        engineers=int(raw["engineers"]),
        worker_salary=float(raw["worker_salary"]),
        engineer_salary=float(raw["engineer_salary"]),
        market_agents_after={str(k): int(v) for k, v in dict(raw["market_agents_after"]).items()},
        previous_management_index=float(raw["previous_management_index"]),
        previous_quality_index=float(raw["previous_quality_index"]),
        worker_avg_salary=float(raw.get("worker_avg_salary", raw["worker_salary"])),
        engineer_avg_salary=float(raw.get("engineer_avg_salary", raw["engineer_salary"])),
        worker_recent=int(raw.get("worker_recent", raw["workers"])),
        worker_mature=int(raw.get("worker_mature", 0)),
        worker_experienced=int(raw.get("worker_experienced", 0)),
        engineer_recent=int(raw.get("engineer_recent", raw["engineers"])),
        engineer_mature=int(raw.get("engineer_mature", 0)),
        engineer_experienced=int(raw.get("engineer_experienced", 0)),
        component_capacity=float(raw.get("component_capacity", 0.0)),
        product_capacity=float(raw.get("product_capacity", 0.0)),
        component_inventory=float(raw.get("component_inventory", 0.0)),
        product_inventory=float(raw.get("product_inventory", 0.0)),
        active_patents=int(raw.get("active_patents", 0)),
        accumulated_research_investment=float(raw.get("accumulated_research_investment", 0.0)),
        last_round_id=str(raw["last_round_id"]) if raw.get("last_round_id") is not None else None,
    )


def _state_to_session(state: CampaignState | None) -> dict[str, object] | None:
    if state is None:
        return None
    return {
        "current_cash": state.current_cash,
        "current_debt": state.current_debt,
        "workers": state.workers,
        "engineers": state.engineers,
        "worker_salary": state.worker_salary,
        "engineer_salary": state.engineer_salary,
        "market_agents_after": state.market_agents_after,
        "previous_management_index": state.previous_management_index,
        "previous_quality_index": state.previous_quality_index,
        "worker_avg_salary": state.worker_avg_salary,
        "engineer_avg_salary": state.engineer_avg_salary,
        "worker_recent": state.worker_recent,
        "worker_mature": state.worker_mature,
        "worker_experienced": state.worker_experienced,
        "engineer_recent": state.engineer_recent,
        "engineer_mature": state.engineer_mature,
        "engineer_experienced": state.engineer_experienced,
        "component_capacity": state.component_capacity,
        "product_capacity": state.product_capacity,
        "component_inventory": state.component_inventory,
        "product_inventory": state.product_inventory,
        "active_patents": state.active_patents,
        "accumulated_research_investment": state.accumulated_research_investment,
        "last_round_id": state.last_round_id,
    }


def _build_all_company_chart_series(raw_rounds: object) -> list[dict[str, object]]:
    if not isinstance(raw_rounds, list):
        return []
    initial_net_assets = 15_000_000.0
    labels: list[str] = ["R0"]
    by_team: dict[str, list[dict[str, object]]] = {}
    for round_point in raw_rounds:
        if not isinstance(round_point, dict):
            continue
        round_id = str(round_point.get("round_id", ""))
        if not round_id:
            continue
        labels.append(round_id.upper())
        if isinstance(round_point.get("net_assets_by_team"), dict):
            standings_by_team = {
                str(team): float(value)
                for team, value in dict(round_point["net_assets_by_team"]).items()
                if value is not None
            }
        else:
            standings = round_point.get("standings", [])
            standings_by_team = {
                str(row["team"]): float(row["net_assets"])
                for row in standings
                if isinstance(row, dict) and row.get("team") is not None and row.get("net_assets") is not None
            }
        all_teams = set(by_team) | set(standings_by_team)
        for team in all_teams:
            by_team.setdefault(team, [])
            if not by_team[team]:
                by_team[team].append({"label": "R0", "value": initial_net_assets})
            value = standings_by_team.get(team)
            by_team[team].append({"label": round_id.upper(), "value": float(value) if value is not None else None})
    def team_sort_key(team: str) -> tuple[int, str]:
        return (0, f"{int(team):03d}") if team.isdigit() else (1, team)
    return [
        {"team": team, "label": f"C{team}", "highlight": team == "13", "points": by_team[team]}
        for team in sorted(by_team.keys(), key=team_sort_key)
    ]


def _all_company_rounds_to_session(raw_rounds: list[dict[str, object]]) -> list[list[object]]:
    compact: list[list[object]] = []
    for round_point in raw_rounds:
        net_assets_by_team = dict(round_point.get("net_assets_by_team", {}))
        compact.append(
            [
                str(round_point["round_id"]),
                [[str(team), int(round(float(value)))] for team, value in net_assets_by_team.items()],
            ]
        )
    return compact


def _all_company_rounds_from_session(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    round_points: list[dict[str, object]] = []
    for item in raw:
        if isinstance(item, dict):
            round_points.append(item)
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2 and isinstance(item[1], list):
            round_points.append(
                {
                    "round_id": str(item[0]),
                    "net_assets_by_team": {
                        str(team): float(value)
                        for team, value in item[1]
                        if team is not None and value is not None
                    },
                }
            )
    return round_points


def _compact_all_company_round_point(report: dict[str, object]) -> dict[str, object]:
    standings = report.get("all_company_standings", [])
    net_assets_by_team = {
        str(row["team"]): float(row["net_assets"])
        for row in standings
        if isinstance(row, dict) and row.get("team") is not None and row.get("net_assets") is not None
    }
    return {
        "round_id": str(report["round_id"]),
        "net_assets_by_team": net_assets_by_team,
    }


def _standings_from_compact_round_point(round_point: object) -> list[dict[str, object]]:
    if not isinstance(round_point, dict):
        return []
    net_assets_by_team = round_point.get("net_assets_by_team")
    if not isinstance(net_assets_by_team, dict):
        return []
    standings = [
        {"team": str(team), "net_assets": float(value)}
        for team, value in dict(net_assets_by_team).items()
        if value is not None
    ]
    standings.sort(key=lambda row: row["net_assets"], reverse=True)
    for idx, row in enumerate(standings, start=1):
        row["rank"] = idx
    return standings


def _next_round(simulator: ExschoolSimulator, round_id: str) -> str | None:
    rounds = simulator.available_rounds()
    try:
        idx = rounds.index(round_id)
    except ValueError as exc:
        raise GameFlowError(f"未知轮次：{round_id}") from exc
    return rounds[idx + 1] if idx + 1 < len(rounds) else None


def _workforce_cohort_snapshot(
    *,
    role_key: str,
    role_label: str,
    total: object,
    current_salary: object,
    average_salary: object,
    recent: object,
    mature: object,
    experienced: object,
) -> dict[str, object]:
    total_count = max(int(total or 0), 0)
    cohort_total = total_count or max(int(recent or 0) + int(mature or 0) + int(experienced or 0), 0)
    cohorts = []
    for label, count in (
        ("新进", max(int(recent or 0), 0)),
        ("稳定", max(int(mature or 0), 0)),
        ("资深", max(int(experienced or 0), 0)),
    ):
        share = float(count) / cohort_total if cohort_total > 0 else 0.0
        cohorts.append(
            {
                "label": label,
                "count": count,
                "share": share,
                "share_label": f"{share:.1%}",
                "badge_label": f"{label} {count} 人 · {share:.1%}",
            }
        )
    return {
        "role_key": role_key,
        "role_label": role_label,
        "total": total_count,
        "current_salary": float(current_salary or 0.0),
        "average_salary": float(average_salary or 0.0),
        "cohorts": cohorts,
    }


def _build_round_page_context(
    simulator: ExschoolSimulator,
    session: dict[str, object],
    *,
    error: str | None = None,
    preserve_form: dict[str, object] | None = None,
    submit_action_url: str | None = None,
    preview_action_url: str | None = None,
    next_action_url: str | None = None,
    multiplayer_room_snapshot: dict[str, Any] | None = None,
) -> dict[str, object]:
    _ensure_current_round_clock(session)
    round_id = str(session["current_round"])
    current_round_submitted = round_id.strip().lower() in _submitted_round_ids(session)
    state = _state_from_session(session.get("campaign_state"))
    mode_key = _single_player_mode_from_session(session)
    round_context = simulator._context_with_campaign_state(
        round_id,
        state,
        current_home_city=str(session.get("home_city", "")),
    )
    loan_limit = float(round_context.get("loan_limit", 0.0) or 0.0)
    payload_loan_delta = float(round_context.get("payload_loan_delta", round_context.get("actual_loan_delta", 0.0)) or 0.0)
    round_context["payload_loan_delta"] = simulator._clamp_loan_delta(payload_loan_delta, round_context)
    payload = simulator._payload_for_context(round_id, round_context)
    if preserve_form is not None:
        payload = preserve_form
    previous_report = None
    reports = _reports_from_session(session.get("reports"))
    if reports:
        previous_report = reports[-1]

    starting_cash = float(round_context.get("starting_cash", simulator.key_data.get("initial_cash", 15_000_000.0)) or 0.0)
    starting_debt = float(round_context.get("starting_debt", 0.0) or 0.0)
    round_started_at_ms = _current_round_started_at_ms(session)
    round_deadline_ms = _current_round_deadline_ms(session)
    current_timestamp_ms = int(time.time() * 1000)
    carry_over_state = {
        "source_round_id": str(round_context.get("campaign_last_round_id", "") or "").strip(),
        "source_round_label": (
            str(round_context.get("campaign_last_round_id", "") or "").strip().upper()
            if str(round_context.get("campaign_last_round_id", "") or "").strip()
            else "初始状态"
        ),
        "component_inventory": float(round_context.get("component_inventory_prev", 0.0) or 0.0),
        "product_inventory": float(round_context.get("product_inventory_prev", 0.0) or 0.0),
        "component_capacity": float(round_context.get("component_capacity_prev", 0.0) or 0.0),
        "product_capacity": float(round_context.get("product_capacity_prev", 0.0) or 0.0),
        "active_patents": int(round_context.get("active_patents_prev", 0) or 0),
        "accumulated_research_investment": float(round_context.get("accumulated_research_investment_prev", 0.0) or 0.0),
        "previous_management_index": float(round_context.get("campaign_previous_management_index", 0.0) or 0.0),
        "previous_quality_index": float(round_context.get("campaign_previous_quality_index", 0.0) or 0.0),
    }
    market_evidence = {
        market: {
            "previous_agents": int(round_context["market_defaults"].get(market, {}).get("previous_agents", 0) or 0),
            "reference_price": float(round_context["market_defaults"].get(market, {}).get("actual_price", 0.0) or 0.0),
            "reference_sales_volume": float(
                round_context["market_defaults"].get(market, {}).get("actual_sales_volume", 0.0) or 0.0
            ),
            "reference_market_share": float(
                round_context["market_defaults"].get(market, {}).get("actual_market_share", 0.0) or 0.0
            ),
        }
        for market in simulator.key_data["markets"].keys()
    }
    current_agents = {
        market: int(round_context["market_defaults"][market].get("previous_agents", 0) or 0)
        for market in simulator.key_data["markets"].keys()
    }
    hr_snapshot = {
        "roles": [
            _workforce_cohort_snapshot(
                role_key="workers",
                role_label="工人",
                total=round_context.get("workers_actual", 0),
                current_salary=round_context.get("worker_salary_actual", 0.0),
                average_salary=round_context.get("worker_avg_salary_prev", round_context.get("worker_salary_actual", 0.0)),
                recent=round_context.get("worker_recent_prev", round_context.get("workers_actual", 0)),
                mature=round_context.get("worker_mature_prev", 0),
                experienced=round_context.get("worker_experienced_prev", 0),
            ),
            _workforce_cohort_snapshot(
                role_key="engineers",
                role_label="工程师",
                total=round_context.get("engineers_actual", 0),
                current_salary=round_context.get("engineer_salary_actual", 0.0),
                average_salary=round_context.get("engineer_avg_salary_prev", round_context.get("engineer_salary_actual", 0.0)),
                recent=round_context.get("engineer_recent_prev", round_context.get("engineers_actual", 0)),
                mature=round_context.get("engineer_mature_prev", 0),
                experienced=round_context.get("engineer_experienced_prev", 0),
            ),
        ]
    }
    worker_avg_salary = float(
        round_context.get("worker_avg_salary_prev", round_context.get("worker_salary_actual", 0.0)) or 0.0
    )
    engineer_avg_salary = float(
        round_context.get("engineer_avg_salary_prev", round_context.get("engineer_salary_actual", 0.0)) or 0.0
    )
    max_repayment = min(starting_cash, starting_debt)
    home_city_display_label = CITY_LABELS.get(str(session.get("home_city", "")), str(session.get("home_city", ""))) or "当前主场"
    active_market_snapshot = [
        f"{CITY_LABELS.get(market, market)} {agents}"
        for market, agents in sorted(current_agents.items(), key=lambda item: (-item[1], item[0]))
        if int(agents) > 0
    ]
    leading_market_name, leading_market = max(
        market_evidence.items(),
        key=lambda item: (item[1]["reference_sales_volume"], item[1]["reference_market_share"], item[1]["previous_agents"], item[0]),
    )
    preview_consequence_hints = [
        {
            "title": "现金会先决定可执行空间",
            "emphasis": f"最多新增贷款 ¥{loan_limit:,.0f} / 最多还款 ¥{max_repayment:,.0f}",
            "detail": (
                f"当前先带入期初现金 ¥{starting_cash:,.0f}、期初负债 ¥{starting_debt:,.0f}；"
                "工资、离职 / 裁员成本会先吃掉现金，后面的生产和投入只按剩余现金继续结算。"
            ),
        },
        {
            "title": "工资变化会先改写到岗与产能",
            "emphasis": (
                f"当前在岗 工人 {int(round_context.get('workers_actual', 0) or 0)}"
                f" / 工程师 {int(round_context.get('engineers_actual', 0) or 0)}"
            ),
            "detail": (
                f"当前平均工资 工人 ¥{worker_avg_salary:,.0f} / 工程师 ¥{engineer_avg_salary:,.0f}；"
                "如果本轮压低工资或缩编，真实执行会先出现离职 / 裁员成本，再下调实际在岗与产能倍率。"
            ),
        },
        {
            "title": "库存与仓储是先带入的硬约束",
            "emphasis": (
                f"零件 {carry_over_state['component_inventory']:,.0f}"
                f" / 成品 {carry_over_state['product_inventory']:,.0f}"
            ),
            "detail": (
                f"当前仓储容量零件 {carry_over_state['component_capacity']:,.0f} / 成品 {carry_over_state['product_capacity']:,.0f}；"
                "这些结转库存会先进入本轮，再和工资倍率一起决定实际零件、成品产出与期末结余。"
            ),
        },
        {
            "title": "主场成本基线会直接进执行",
            "emphasis": f"{home_city_display_label} 利率 {float(round_context.get('interest_rate', 0.0) or 0.0):.1%}",
            "detail": (
                f"当前主场同时带入零件材料 ¥{float(round_context.get('component_material_price', 0.0) or 0.0):,.0f}"
                f" / 成品材料 ¥{float(round_context.get('product_material_price', 0.0) or 0.0):,.0f}"
                f" / 仓储 零件 ¥{float(round_context.get('component_storage_unit_cost', 0.0) or 0.0):,.0f}"
                f" / 成品 ¥{float(round_context.get('product_storage_unit_cost', 0.0) or 0.0):,.0f}；"
                "这些都是真实结算成本，不只是题面参考。"
            ),
        },
        {
            "title": "代理底盘和研发累积都会续接",
            "emphasis": (
                " / ".join(active_market_snapshot[:3])
                if active_market_snapshot
                else "当前没有续接代理"
            ),
            "detail": (
                f"当前代理最多的是 {CITY_LABELS.get(leading_market_name, leading_market_name)}，参考销量 {leading_market['reference_sales_volume']:,.0f}"
                f"、参考份额 {leading_market['reference_market_share']:.1%}；"
                f"累计研发 ¥{carry_over_state['accumulated_research_investment']:,.0f}、已有 {carry_over_state['active_patents']} 项专利、"
                f"管理指数 {carry_over_state['previous_management_index']:.2f}、质量指数 {carry_over_state['previous_quality_index']:.2f} 会一起影响本轮执行。"
            ),
        },
    ]
    decision_near_guidance = {
        "investments": [
            {
                "title": "研发投入不会在本轮立刻降成本",
                "emphasis": (
                    f"当前累计研发 ¥{carry_over_state['accumulated_research_investment']:,.0f}"
                    f" / 已有 {carry_over_state['active_patents']} 项专利"
                ),
                "detail": (
                    "研发投入会先累计，再按 KDS 拟合出的概率函数触发专利；"
                    "新专利从下一轮开始降低材料成本，不会在本轮即时回收。"
                ),
            },
            {
                "title": "管理和质量是在续接基线上加码",
                "emphasis": (
                    f"当前管理指数 {carry_over_state['previous_management_index']:.2f}"
                    f" / 质量指数 {carry_over_state['previous_quality_index']:.2f}"
                ),
                "detail": "这里的投入是在上一轮状态上继续累计影响，不是从零开始重算。",
            },
        ],
        "markets": [
            {
                "title": "市场参考是定锚，不是本轮预测",
                "emphasis": (
                    f"当前参考强市 {CITY_LABELS.get(leading_market_name, leading_market_name)}"
                    f" · 份额 {leading_market['reference_market_share']:.1%}"
                ),
                "detail": "下方参考价格、销量和份额来自题面/工作簿参考；本轮真实结果仍由你的代理、营销、定价和续接底盘共同决定。",
            },
            {
                "title": "订阅市场报表会单独计费",
                "emphasis": "每订阅 1 个城市额外支付 ¥200,000",
                "detail": "未订阅城市不会在本轮报告里显示市场报表，所以订阅选择本身就是一项经营取舍。",
            },
        ],
    }

    multiplayer_participant_count = None
    if isinstance(multiplayer_room_snapshot, dict):
        multiplayer_participant_count = len(multiplayer_room_snapshot.get("players", [])) + len(multiplayer_room_snapshot.get("bots", []))

    return {
        "round_id": round_id,
        "round_number": simulator.available_rounds().index(round_id) + 1,
        "all_rounds": simulator.available_rounds(),
        "round_statuses": _round_statuses(simulator, session, current_round=round_id),
        "all_markets": list(simulator.key_data["markets"].keys()),
        "payload_json": payload,
        "key_data": simulator.key_data,
        "company_name": session.get("company_name", ""),
        "home_city": session.get("home_city", ""),
        "home_city_label": CITY_LABELS.get(str(session.get("home_city", "")), str(session.get("home_city", ""))),
        "time_limit_seconds": session.get("time_limit_seconds", GAME_DURATION_SECONDS),
        "game_id": session.get("game_id", "default"),
        "submit_action_url": submit_action_url or "/game/submit",
        "preview_action_url": preview_action_url or "/game/preview",
        "next_action_url": next_action_url or "/game/next",
        "single_player_mode": mode_key,
        "single_player_mode_label": _single_player_mode_label(mode_key),
        "starting_cash": starting_cash,
        "starting_debt": starting_debt,
        "loan_limit": loan_limit,
        "carry_over_state": carry_over_state,
        "workers_actual": int(round_context.get("workers_actual", 0) or 0),
        "engineers_actual": int(round_context.get("engineers_actual", 0) or 0),
        "hr_snapshot": hr_snapshot,
        "current_agents": current_agents,
        "market_evidence": market_evidence,
        "round_started_at_ms": round_started_at_ms,
        "round_deadline_ms": round_deadline_ms,
        "current_timestamp_ms": current_timestamp_ms,
        "round_is_expired": bool(
            round_deadline_ms is not None and current_timestamp_ms > round_deadline_ms + ROUND_TIMEOUT_AUTO_SUBMIT_GRACE_SECONDS * 1000
        ),
        "preview_consequence_hints": preview_consequence_hints,
        "decision_near_guidance": decision_near_guidance,
        "error": error,
        "previous_report": previous_report,
        "current_round_submitted": current_round_submitted,
        "multiplayer_room_snapshot": multiplayer_room_snapshot,
        "is_multiplayer": bool(session.get("multiplayer_room_id")),
        "multiplayer_participant_count": multiplayer_participant_count,
    }


def _find_finance_row(report: dict[str, object], label: str) -> tuple[str, float | None, float | None, float | None, float | None] | None:
    for row in report.get("finance_rows", []):
        if not isinstance(row, (list, tuple)) or len(row) < 5:
            continue
        if str(row[0]) == label:
            return (
                str(row[0]),
                float(row[1]) if row[1] is not None else None,
                float(row[2]) if row[2] is not None else None,
                float(row[3]) if row[3] is not None else None,
                float(row[4]) if row[4] is not None else None,
            )
    return None


def _build_round_preview_payload(decision: Any, report: dict[str, object]) -> dict[str, object]:
    loan_row = _find_finance_row(report, "银行贷款 / 还款")
    market_report_cost_row = _find_finance_row(report, "市场报告费用")
    products_row = next(
        (
            row
            for row in report.get("production_overview", [])
            if isinstance(row, dict) and str(row.get("item")) == "Products"
        ),
        {},
    )
    components_row = next(
        (
            row
            for row in report.get("production_overview", [])
            if isinstance(row, dict) and str(row.get("item")) == "Components"
        ),
        {},
    )
    worker_row = next(
        (
            row
            for row in report.get("hr_detail", [])
            if isinstance(row, dict) and str(row.get("category")) == "Workers"
        ),
        {},
    )
    engineer_row = next(
        (
            row
            for row in report.get("hr_detail", [])
            if isinstance(row, dict) and str(row.get("category")) == "Engineers"
        ),
        {},
    )
    requested_subscribed_markets = [
        market
        for market, market_decision in dict(decision.market_decisions).items()
        if getattr(market_decision, "subscribed_market_report", False)
    ]
    effective_subscribed_markets = [str(market) for market in (report.get("market_report_subscriptions", []) or [])]
    dropped_subscribed_markets = [
        market for market in requested_subscribed_markets if market not in set(effective_subscribed_markets)
    ]
    report_markets = {
        str(row.get("market")): row
        for row in report.get("market_results", [])
        if isinstance(row, dict) and row.get("market") is not None
    }

    def _workforce_preview(label: str, requested_total_after: int, row: dict[str, object]) -> dict[str, object]:
        starting_total = int(row.get("previous", 0) or 0) + int(row.get("previous_experienced", 0) or 0)
        return {
            "category": label,
            "starting_total": starting_total,
            "requested_change": int(requested_total_after) - starting_total,
            "requested_total_after": int(requested_total_after),
            "effective_total": int(row.get("working_total", 0) or 0),
            "laid_off": int(row.get("laid_off", 0) or 0),
            "quits": int(row.get("quits", 0) or 0),
            "added": int(row.get("added", 0) or 0),
            "salary": float(row.get("salary", 0.0) or 0.0),
            "average_salary": float(row.get("avg", 0.0) or 0.0),
            "salary_ratio": float(row.get("salary_ratio", 1.0) or 0.0),
            "productivity_multiplier": float(row.get("productivity_multiplier", 1.0) or 0.0),
        }

    markets: list[dict[str, object]] = []
    for market, requested in dict(decision.market_decisions).items():
        effective = report_markets.get(str(market), {})
        previous_agents = int(effective.get("agents_before", 0) or 0)
        requested_agent_change = int(getattr(requested, "agent_change", 0) or 0)
        markets.append(
            {
                "market": str(market),
                "previous_agents": previous_agents,
                "requested_subscribed": bool(getattr(requested, "subscribed_market_report", False)),
                "effective_subscribed": bool(effective.get("subscribed_market_report", False)),
                "requested_agent_change": requested_agent_change,
                "effective_agent_change": int(effective.get("agent_change", 0) or 0),
                "requested_agents_after": max(previous_agents + requested_agent_change, 0),
                "effective_agents_after": int(effective.get("agents_after", 0) or 0),
                "requested_marketing_investment": float(getattr(requested, "marketing_investment", 0.0) or 0.0),
                "effective_marketing_investment": float(effective.get("marketing_investment", 0.0) or 0.0),
                "price": float(effective.get("price", getattr(requested, "price", 0.0)) or 0.0),
                "sales_volume": float(effective.get("sales_volume", 0.0) or 0.0),
                "market_share": float(effective.get("market_share", 0.0) or 0.0),
            }
        )

    return {
        "market_report_source": copy.deepcopy(report.get("market_report_source", {}))
        if isinstance(report.get("market_report_source"), dict)
        else {},
        "loan": {
            "requested": float(decision.loan_delta),
            "effective": float(loan_row[1] or 0.0) if loan_row else 0.0,
            "starting_cash": float(report.get("starting_cash", 0.0) or 0.0),
            "starting_debt": float(report.get("starting_debt", 0.0) or 0.0),
            "ending_cash": float(report.get("ending_cash", 0.0) or 0.0),
            "ending_debt": float(report.get("ending_debt", 0.0) or 0.0),
        },
        "production": {
            "requested_products": int(getattr(decision, "products_planned", 0) or 0),
            "actual_products": float(products_row.get("produced", 0.0) or 0.0),
            "actual_components": float(components_row.get("produced", 0.0) or 0.0),
            "product_capacity": float(report.get("production_summary", {}).get("成品最大产能", 0.0) or 0.0),
            "component_capacity": float(report.get("production_summary", {}).get("零件最大产能", 0.0) or 0.0),
            "leftover_products": float(report.get("product_inventory_end", 0.0) or 0.0),
            "leftover_components": float(report.get("component_inventory_end", 0.0) or 0.0),
        },
        "investments": {
            "management_requested": float(getattr(decision, "management_investment", 0.0) or 0.0),
            "management_effective": float(report.get("management_summary", {}).get("investment", 0.0) or 0.0),
            "quality_requested": float(getattr(decision, "quality_investment", 0.0) or 0.0),
            "quality_effective": float(report.get("production_summary", {}).get("质量投资", 0.0) or 0.0),
            "subscriptions_requested": len(requested_subscribed_markets),
            "subscriptions_effective": len(effective_subscribed_markets),
            "subscriptions_requested_markets": requested_subscribed_markets,
            "subscriptions_effective_markets": effective_subscribed_markets,
            "subscriptions_dropped_markets": dropped_subscribed_markets,
            "subscriptions_truncation_rule": (
                "若现金不足，当前会按页面城市顺序仅保留前 N 个已勾选城市。"
                if dropped_subscribed_markets
                else ""
            ),
            "market_report_cost_effective": abs(float(market_report_cost_row[1] or 0.0)) if market_report_cost_row else 0.0,
        },
        "workforce": [
            _workforce_preview("工人", int(getattr(decision, "workers", 0) or 0), worker_row),
            _workforce_preview("工程师", int(getattr(decision, "engineers", 0) or 0), engineer_row),
        ],
        "markets": markets,
    }


def _report_image_cache_key(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _report_image_cache_path_from_key(cache_key: str, *, client_id: str | None = None) -> Path:
    REPORT_IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    normalized_client_id = str(client_id or "").strip()
    if not normalized_client_id:
        return REPORT_IMAGE_CACHE_DIR / f"{cache_key}.png"
    owner_prefix = hashlib.sha256(normalized_client_id.encode("utf-8")).hexdigest()[:16]
    return REPORT_IMAGE_CACHE_DIR / f"{owner_prefix}_{cache_key}.png"


def _report_image_cache_path(html: str, *, client_id: str | None = None) -> Path:
    return _report_image_cache_path_from_key(_report_image_cache_key(html), client_id=client_id)


def _render_report_image_bytes(html: str) -> bytes:
    REPORT_IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    html_fd, html_raw_path = tempfile.mkstemp(prefix="exschool_report_", suffix=".html", dir=str(REPORT_IMAGE_CACHE_DIR))
    png_fd, png_raw_path = tempfile.mkstemp(prefix="exschool_report_", suffix=".png", dir=str(REPORT_IMAGE_CACHE_DIR))
    html_path = Path(html_raw_path)
    png_path = Path(png_raw_path)
    os.close(html_fd)
    os.close(png_fd)
    try:
        html_path.write_text(html, encoding="utf-8")
        cmd = [sys.executable, str(SCREENSHOT_SCRIPT), "--html", str(html_path), "--output", str(png_path)]
        subprocess.run(cmd, check=True, cwd=str(BASE_DIR.parent), timeout=45)
        return png_path.read_bytes()
    finally:
        for path in (html_path, png_path):
            try:
                path.unlink()
            except FileNotFoundError:
                continue


def _prune_report_image_cache(now: float | None = None) -> None:
    REPORT_IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    current_time = now if now is not None else time.time()
    ttl_cutoff = current_time - REPORT_IMAGE_CACHE_TTL_SECONDS
    png_files = [path for path in REPORT_IMAGE_CACHE_DIR.glob("*.png") if path.is_file()]

    survivors: list[tuple[float, Path]] = []
    for path in png_files:
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < ttl_cutoff:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        survivors.append((stat.st_mtime, path))

    if len(survivors) <= REPORT_IMAGE_CACHE_MAX_FILES:
        return
    survivors.sort(key=lambda item: item[0])
    for _mtime, path in survivors[: max(0, len(survivors) - REPORT_IMAGE_CACHE_MAX_FILES)]:
        try:
            path.unlink()
        except OSError:
            continue


def _prime_report_image_cache(html: str, *, client_id: str | None = None) -> None:
    if os.environ.get("EXSCHOOL_DISABLE_REPORT_IMAGE_PREWARM") == "1":
        return
    _prune_report_image_cache()
    cache_key = _report_image_cache_key(html)
    cached_png_path = _report_image_cache_path_from_key(cache_key, client_id=client_id)
    if cached_png_path.exists():
        return

    def worker() -> None:
        if cached_png_path.exists():
            return
        try:
            png_bytes = _render_report_image_bytes(html)
            cached_png_path.write_bytes(png_bytes)
        except Exception:
            logger.warning("report image prewarm failed for cache_key=%s", cache_key, exc_info=True)
            return

    threading.Thread(target=worker, daemon=True).start()


def _report_export_artifacts(
    simulator: ExschoolSimulator,
    session: dict[str, object],
    report: dict[str, object],
) -> tuple[str, str]:
    export_report_html = render_report_html(
        {
            "company_name": session.get("company_name", ""),
            "home_city": session.get("home_city", ""),
            "home_city_label": CITY_LABELS.get(str(session.get("home_city", "")), str(session.get("home_city", ""))),
            "single_player_mode": _single_player_mode_from_session(session),
            "single_player_mode_label": _single_player_mode_label(_single_player_mode_from_session(session)),
            "key_data": simulator.key_data,
            "report": report,
        }
    )
    report_image_cache_key = _report_image_cache_key(export_report_html)
    return export_report_html, report_image_cache_key


def _build_report_page_context(
    simulator: ExschoolSimulator,
    session: dict[str, object],
    report: dict[str, object],
    *,
    cache_owner_client_id: str | None = None,
    next_action_url: str | None = None,
    next_action_label: str | None = None,
) -> dict[str, object]:
    reports = _reports_from_session(session.get("reports"))
    export_report_html, report_image_cache_key = _report_export_artifacts(simulator, session, report)
    _remember_report_image_cache_key(session, round_id=report.get("round_id"), cache_key=report_image_cache_key)
    _prime_report_image_cache(export_report_html, client_id=cache_owner_client_id)
    return {
        "report": report,
        "market_report_source": copy.deepcopy(report.get("market_report_source", {}))
        if isinstance(report.get("market_report_source"), dict)
        else {},
        "game_id": session.get("game_id", "default"),
        "company_name": session.get("company_name", ""),
        "home_city": session.get("home_city", ""),
        "home_city_label": CITY_LABELS.get(str(session.get("home_city", "")), str(session.get("home_city", ""))),
        "current_team_id": str(session.get("multiplayer_team_id", TEAM_ID)),
        "is_multiplayer": bool(session.get("multiplayer_room_id")),
        "multiplayer_participant_count": len(report.get("all_company_standings", [])),
        "single_player_mode": _single_player_mode_from_session(session),
        "single_player_mode_label": _single_player_mode_label(_single_player_mode_from_session(session)),
        "next_round": _next_round(simulator, report["round_id"]),
        "final_round": _next_round(simulator, report["round_id"]) is None,
        "next_action_url": next_action_url or "/game/next",
        "next_action_label": next_action_label or ("查看总结" if _next_round(simulator, report["round_id"]) is None else "进入下一轮决策"),
        "all_reports": reports,
        "round_statuses": _round_statuses(simulator, session, current_round=str(report["round_id"])),
        "all_company_chart_series": _build_all_company_chart_series(_all_company_rounds_from_session(session.get("all_company_rounds"))),
        "export_report_html": export_report_html,
        "report_image_cache_key": report_image_cache_key,
    }


def _build_final_page_context(
    *,
    company_name: str,
    home_city: str,
    single_player_mode: str,
    reports: list[dict[str, object]],
    all_company_rounds: list[dict[str, object]],
) -> dict[str, object]:
    total_revenue = sum(float(report["key_metrics"].get("销售收入", 0.0) or 0.0) for report in reports)
    total_net_profit = sum(float(report.get("net_profit", 0.0) or 0.0) for report in reports)
    final_net_assets = float(reports[-1].get("net_assets", 0.0) or 0.0) if reports else 0.0
    net_assets_series = [
        {
            "label": str(report["round_id"]).upper(),
            "value": float(report.get("net_assets", 0.0) or 0.0),
        }
        for report in reports
    ]
    compact_rounds = _all_company_rounds_to_session(all_company_rounds)
    return {
        "company_name": company_name,
        "home_city": home_city,
        "home_city_label": CITY_LABELS.get(str(home_city), str(home_city)),
        "single_player_mode": _normalize_single_player_mode(single_player_mode),
        "single_player_mode_label": _single_player_mode_label(single_player_mode),
        "reports": reports,
        "total_revenue": total_revenue,
        "total_net_profit": total_net_profit,
        "final_net_assets": final_net_assets,
        "net_assets_series": net_assets_series,
        "all_company_chart_series": _build_all_company_chart_series(all_company_rounds),
        "final_standings": _standings_from_compact_round_point((all_company_rounds or [{}])[-1]),
        "completed_game_count": len(reports),
        "all_company_rounds_compact": compact_rounds,
    }


def _build_final_page_context_from_session(session: dict[str, object]) -> dict[str, object]:
    return _build_final_page_context(
        company_name=str(session.get("company_name", "")),
        home_city=str(session.get("home_city", "")),
        single_player_mode=str(session.get("single_player_mode", "")),
        reports=_reports_from_session(session.get("reports")),
        all_company_rounds=_all_company_rounds_from_session(session.get("all_company_rounds")),
    )


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    return _template_response(request, "home.html", {})


@app.get("/auth", response_class=HTMLResponse)
async def auth_page(request: Request, mode: str = "login") -> HTMLResponse:
    if _current_user(request):
        return RedirectResponse(url=str(request.url_for("home")), status_code=303)
    auth_mode = mode if mode in {"login", "register"} else "login"
    registered = request.query_params.get("registered") == "1"
    account = str(request.query_params.get("account", "")).strip()
    return _template_response(
        request,
        "auth.html",
        {
            "title": "登录或注册",
            "minimal_auth_page": True,
            "auth_mode": auth_mode,
            "auth_error": None,
            "auth_message": "注册成功，请登录。" if registered else None,
            "auth_values": {"account": account} if account else {},
        },
    )


@app.post("/auth/email-code")
async def auth_send_email_code(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "detail": "请求体不是有效的 JSON。"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "detail": "请求体必须是 JSON 对象。"}, status_code=400)
    try:
        _require_csrf_token(request, payload.get("_csrf") or payload.get("csrf_token") or request.headers.get("x-csrf-token"))
    except CsrfError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=403)
    email = str(payload.get("email", "")).strip()
    purpose = str(payload.get("purpose", "register")).strip() or "register"
    try:
        cooldown = send_email_code(email, purpose, client_ip=_client_ip(request))
    except AuthServiceUnavailableError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=503)
    except AuthClientError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "ok": True,
            "message": "验证码已发送，请检查邮箱。",
            "cooldown_seconds": cooldown,
        }
    )


@app.post("/auth/register", response_class=HTMLResponse)
async def auth_register(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return _template_response(
            request,
            "auth.html",
            {
                "title": "注册账号",
                "minimal_auth_page": True,
                "auth_mode": "register",
                "auth_error": str(exc),
                "auth_message": None,
                "auth_values": {"name": str(form.get("name", "")).strip(), "email": str(form.get("email", "")).strip()},
            },
            status_code=403,
        )
    name = str(form.get("name", "")).strip()
    email = str(form.get("email", "")).strip()
    code = str(form.get("code", "")).strip()
    password = str(form.get("password", ""))
    try:
        result = register_user(name, email, code, password, client_ip=_client_ip(request))
    except AuthClientError as exc:
        return _template_response(
            request,
            "auth.html",
            {
                "title": "注册账号",
                "minimal_auth_page": True,
                "auth_mode": "register",
                "auth_error": str(exc),
                "auth_message": None,
                "auth_values": {"name": name, "email": email},
            },
            status_code=400,
        )
    _clear_auth_session(request)
    login_url = str(request.url_for("auth_page"))
    preferred_account = result.user.email or result.user.name
    redirect_url = f"{login_url}?mode=login&registered=1&account={quote(preferred_account)}"
    return RedirectResponse(url=redirect_url, status_code=303)


@app.post("/auth/login", response_class=HTMLResponse)
async def auth_login(request: Request) -> HTMLResponse:
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return _template_response(
            request,
            "auth.html",
            {
                "title": "登录账号",
                "minimal_auth_page": True,
                "auth_mode": "login",
                "auth_error": str(exc),
                "auth_message": None,
                "auth_values": {"account": str(form.get("account", "")).strip()},
            },
            status_code=403,
        )
    account = str(form.get("account", "")).strip()
    password = str(form.get("password", ""))
    try:
        result = login_user(account, password, client_ip=_client_ip(request))
    except AuthClientError as exc:
        return _template_response(
            request,
            "auth.html",
            {
                "title": "登录账号",
                "minimal_auth_page": True,
                "auth_mode": "login",
                "auth_error": str(exc),
                "auth_message": None,
                "auth_values": {"account": account},
            },
            status_code=400,
        )
    _save_auth_session(request, result)
    active_session = user_game_store.get_active_game_session(result.user.client_id)
    _set_selected_game_id(request, str(active_session.get("game_id", "")).strip() if active_session else None)
    return RedirectResponse(url=str(request.url_for("home")), status_code=303)


@app.post("/auth/logout")
async def auth_logout(request: Request) -> Response:
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return Response(content=str(exc), status_code=403)
    _clear_auth_session(request)
    return RedirectResponse(url=str(request.url_for("home")), status_code=303)


@app.get("/mode", response_class=HTMLResponse)
async def mode_select(request: Request) -> HTMLResponse:
    if not _current_user(request):
        return _auth_redirect(request)
    return _template_response(request, "mode.html", {})


def _render_multiplayer_setup(
    request: Request,
    *,
    create_error: str | None = None,
    join_error: str | None = None,
    form_values: dict[str, object] | None = None,
    join_values: dict[str, object] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    values = form_values or {}
    room_values = join_values or {}
    return _template_response(
        request,
        "multiplayer_setup.html",
        {
            "create_error": create_error,
            "join_error": join_error,
            "setup_values": {
                "seat_limit": int(values.get("seat_limit", 2) or 2),
                "bot_count": int(values.get("bot_count", 0) or 0),
            },
            "join_values": {
                "room_code": str(room_values.get("room_code", "") or "").strip().upper(),
            },
        },
        status_code=status_code,
    )


def _multiplayer_page_data(request: Request, room: dict[str, Any], *, user: dict[str, str]) -> dict[str, Any]:
    room_id = str(room.get("room_id", "")).strip()
    room_code = str(room.get("room_code", "")).strip().upper() or room_id.upper()
    return {
        "roomId": room_id,
        "roomCode": room_code,
        "currentUserName": str(user.get("name", "")).strip(),
        "homeCityOptions": HOME_CITY_OPTIONS,
        "homeCityLabels": CITY_LABELS,
        "pollIntervalMs": MULTIPLAYER_ROOM_POLL_INTERVAL_MS,
        "initialSnapshot": _room_snapshot(room, current_client_id=user["client_id"]),
        "actions": {
            "snapshotUrl": str(request.url_for("multiplayer_room_snapshot_api", room_id=room_id)),
            "joinUrl": str(request.url_for("multiplayer_join_room", room_id=room_id)),
            "leaveUrl": str(request.url_for("multiplayer_leave_room", room_id=room_id)),
            "toggleReadyUrl": str(request.url_for("multiplayer_toggle_ready", room_id=room_id)),
            "homeCityUrl": str(request.url_for("multiplayer_update_home_city", room_id=room_id)),
            "startUrl": str(request.url_for("multiplayer_start_room", room_id=room_id)),
            "gameUrl": str(request.url_for("multiplayer_room_game", room_id=room_id)),
            "reportUrl": str(request.url_for("multiplayer_room_report", room_id=room_id)),
            "finalUrl": str(request.url_for("multiplayer_room_final", room_id=room_id)),
        },
    }


@app.get("/multi/setup", response_class=HTMLResponse)
async def multiplayer_setup(request: Request) -> HTMLResponse:
    if not _current_user(request):
        return _auth_redirect(request)
    return _render_multiplayer_setup(request)


@app.post("/multi/join")
async def multiplayer_join_by_code(request: Request) -> Response:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    form = await request.form()
    room_code = str(form.get("room_code", "") or "").strip().upper()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return _render_multiplayer_setup(
            request,
            join_error=str(exc),
            join_values={"room_code": room_code},
            status_code=403,
        )
    if not room_code:
        return _render_multiplayer_setup(
            request,
            join_error="请输入房间码。",
            join_values={"room_code": room_code},
            status_code=422,
        )
    room = multiplayer_room_store.get_room_raw(room_code)
    if room is None:
        return _render_multiplayer_setup(
            request,
            join_error="未找到该房间码对应的多人房间。",
            join_values={"room_code": room_code},
            status_code=404,
        )
    try:
        multiplayer_room_store.join_room(room_code, user)
    except ValueError as exc:
        return _render_multiplayer_setup(
            request,
            join_error=str(exc),
            join_values={"room_code": room_code},
            status_code=409,
        )
    joined_room = multiplayer_room_store.get_room_raw(room_code)
    if joined_room is None:
        return _render_multiplayer_setup(
            request,
            join_error="加入成功后未能重新读取房间。",
            join_values={"room_code": room_code},
            status_code=500,
        )
    return RedirectResponse(
        url=str(request.url_for("multiplayer_room_page", room_id=str(joined_room.get("room_id", "")))),
        status_code=303,
    )


@app.post("/multi/rooms")
async def multiplayer_create_room(request: Request) -> Response:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return _render_multiplayer_setup(
            request,
            create_error=str(exc),
            form_values={"seat_limit": form.get("seat_limit"), "bot_count": form.get("bot_count")},
            status_code=403,
        )
    try:
        seat_limit = int(form.get("seat_limit", 2) or 2)
        bot_count = int(form.get("bot_count", 0) or 0)
    except (TypeError, ValueError):
        return _render_multiplayer_setup(
            request,
            create_error="人数和 bot 数量必须是整数。",
            form_values={"seat_limit": form.get("seat_limit"), "bot_count": form.get("bot_count")},
            status_code=422,
        )
    if not (1 <= seat_limit <= 6):
        return _render_multiplayer_setup(
            request,
            create_error="房间人数上限必须在 1 到 6 之间。",
            form_values={"seat_limit": seat_limit, "bot_count": bot_count},
            status_code=422,
        )
    max_bots = max(0, len(_multiplayer_team_order()) - seat_limit)
    if not (0 <= bot_count <= max_bots):
        return _render_multiplayer_setup(
            request,
            create_error=f"当前最多只能添加 {max_bots} 个 bot。",
            form_values={"seat_limit": seat_limit, "bot_count": bot_count},
            status_code=422,
        )
    room = multiplayer_room_store.create_room(
        user,
        seat_count=seat_limit + bot_count,
        bot_count=bot_count,
        metadata={"human_seat_limit": seat_limit},
        status="lobby",
        time_limit_seconds=GAME_DURATION_SECONDS,
    )
    return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room["room_id"])), status_code=303)


@app.get("/multi/rooms/{room_id}", response_class=HTMLResponse)
async def multiplayer_room_page(request: Request, room_id: str) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return _template_response(request, "home.html", {"history_error": "未找到该多人房间。"}, status_code=404)
    room = _ensure_room_progress(room)
    return _template_response(
        request,
        "multiplayer_room.html",
        {
            "multiplayer_page_data": _multiplayer_page_data(request, room, user=user),
        },
    )


@app.get("/api/multi/rooms/{room_id}")
async def multiplayer_room_snapshot_api(request: Request, room_id: str) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    room = _ensure_room_progress(room)
    return _room_json_response(room, current_client_id=user["client_id"])


@app.post("/api/multi/rooms/{room_id}/join")
async def multiplayer_join_room(request: Request, room_id: str) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    try:
        _require_csrf_token(request, request.headers.get("x-csrf-token"))
    except CsrfError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    try:
        multiplayer_room_store.join_room(room_id, user)
    except ValueError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=409)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    return _room_json_response(room, current_client_id=user["client_id"], message="已加入房间。")


@app.post("/api/multi/rooms/{room_id}/leave")
async def multiplayer_leave_room(request: Request, room_id: str) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    try:
        _require_csrf_token(request, request.headers.get("x-csrf-token"))
    except CsrfError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    room = multiplayer_room_store.leave_room(room_id, user["client_id"])
    if room is None:
        return JSONResponse({"room_id": room_id, "status": "closed", "message": "房间已解散。"})
    raw_room = multiplayer_room_store.get_room_raw(room_id)
    if raw_room is None:
        return JSONResponse({"room_id": room_id, "status": "closed", "message": "房间已解散。"})
    return _room_json_response(raw_room, current_client_id=user["client_id"], message="已离开房间。")


@app.post("/api/multi/rooms/{room_id}/ready")
async def multiplayer_toggle_ready(request: Request, room_id: str) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    try:
        _require_csrf_token(request, request.headers.get("x-csrf-token"))
    except CsrfError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    if str(room.get("status", "lobby")).strip().lower() != "lobby":
        return JSONResponse({"detail": "房间已经开始，不能再调整准备状态。"}, status_code=409)
    body = await request.json()
    player = _room_player(room, user["client_id"])
    if player is None:
        return JSONResponse({"detail": "你还没有加入房间。"}, status_code=409)
    next_ready = bool(body.get("ready", not bool(player.get("ready", player.get("is_ready", False)))))
    multiplayer_room_store.set_member_ready(room_id, user["client_id"], next_ready)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    return _room_json_response(room, current_client_id=user["client_id"], message="准备状态已更新。")


@app.post("/api/multi/rooms/{room_id}/home-city")
async def multiplayer_update_home_city(request: Request, room_id: str) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    try:
        _require_csrf_token(request, request.headers.get("x-csrf-token"))
    except CsrfError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    if str(room.get("status", "lobby")).strip().lower() != "lobby":
        return JSONResponse({"detail": "房间已经开始，不能再调整主场城市。"}, status_code=409)
    player = _room_player(room, user["client_id"])
    if player is None:
        return JSONResponse({"detail": "你还没有加入房间。"}, status_code=409)
    body = await request.json()
    home_city = str(body.get("home_city", "") or "").strip()
    if home_city not in HOME_CITY_OPTIONS:
        return JSONResponse({"detail": "请选择有效的主场城市。"}, status_code=422)
    multiplayer_room_store.update_member(room_id, user["client_id"], {"home_city": home_city})
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    return _room_json_response(room, current_client_id=user["client_id"], message="主场城市已更新。")


@app.post("/api/multi/rooms/{room_id}/start")
async def multiplayer_start_room(request: Request, room_id: str) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    try:
        _require_csrf_token(request, request.headers.get("x-csrf-token"))
    except CsrfError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    if str(room.get("host_client_id", "")).strip() != str(user.get("client_id", "")).strip():
        return JSONResponse({"detail": "只有房主可以开始对局。"}, status_code=403)
    if not _room_is_full(room):
        return JSONResponse({"detail": "房间未满，不能开始。"}, status_code=409)
    if not _room_all_ready(room):
        return JSONResponse({"detail": "仍有玩家未准备，不能开始。"}, status_code=409)
    now_ms = int(time.time() * 1000)
    updated_room = dict(room)
    updated_room["status"] = "active"
    updated_room["current_round"] = "r1"
    updated_room["round_started_at_ms_by_round"] = {"r1": now_ms}
    updated_room["pending_submissions"] = {}
    updated_room["human_team_order"] = _room_human_team_preview(room)
    updated_room["bot_team_order"] = _room_bot_team_preview(room)
    updated_room["team_states"] = _initial_room_team_states(updated_room)
    updated_room["latest_reports_by_team"] = {}
    updated_room["latest_round_id"] = None
    updated_players = []
    for idx, player in enumerate(_room_members(updated_room)):
        refreshed = dict(player)
        team_id = updated_room["human_team_order"][idx] if idx < len(updated_room["human_team_order"]) else None
        refreshed["team_id"] = team_id
        refreshed["ready"] = False
        refreshed["is_ready"] = False
        refreshed["reports"] = []
        refreshed["all_company_rounds"] = []
        refreshed["latest_report_detail"] = None
        refreshed["submitted_round_ids"] = []
        refreshed["updated_at"] = time.time()
        updated_players.append(refreshed)
    updated_room["members"] = updated_players
    updated_room = multiplayer_room_store.save_room(updated_room)
    return _room_json_response(updated_room, current_client_id=user["client_id"], message="房间已开始。")


@app.get("/multi/rooms/{room_id}/game", response_class=HTMLResponse)
async def multiplayer_room_game(request: Request, room_id: str) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return _template_response(request, "home.html", {"history_error": "未找到该多人房间。"}, status_code=404)
    room = _ensure_room_progress(room)
    if str(room.get("status", "")).strip().lower() == "report":
        return RedirectResponse(url=str(request.url_for("multiplayer_room_report", room_id=room_id)), status_code=303)
    if str(room.get("status", "")).strip().lower() == "finished":
        return RedirectResponse(url=str(request.url_for("multiplayer_room_final", room_id=room_id)), status_code=303)
    assignment = next((item for item in _room_assignments(room) if item["client_id"] == user["client_id"]), None)
    if assignment is None:
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    player = _room_player(room, user["client_id"])
    if not isinstance(player, dict):
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    current_round = str(room.get("current_round", "r1")).strip().lower()
    pending_submissions = dict(room.get("pending_submissions", {})) if isinstance(room.get("pending_submissions"), dict) else {}
    current_round_submissions = dict(pending_submissions.get(current_round, {})) if isinstance(pending_submissions.get(current_round, {}), dict) else {}
    if assignment["team_id"] in current_round_submissions:
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    session = _player_room_session(room, player, assignment["team_id"])
    simulator = _simulator("real-original")
    room_context = _build_round_page_context(
        simulator,
        session,
        submit_action_url=str(request.url_for("multiplayer_room_submit", room_id=room_id)),
        preview_action_url=str(request.url_for("multiplayer_room_preview", room_id=room_id)),
        next_action_url=str(request.url_for("multiplayer_room_next", room_id=room_id)),
        multiplayer_room_snapshot=_room_snapshot(room, current_client_id=user["client_id"]),
    )
    return _template_response(request, "round.html", room_context)


@app.post("/multi/rooms/{room_id}/preview")
async def multiplayer_room_preview(request: Request, room_id: str) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    try:
        payload = await request.form()
        _require_csrf_token(request, payload.get("_csrf"))
    except CsrfError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=403)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return JSONResponse({"detail": "未找到该多人房间。"}, status_code=404)
    room = _ensure_room_progress(room)
    assignment = next((item for item in _room_assignments(room) if item["client_id"] == user["client_id"]), None)
    if assignment is None:
        return JSONResponse({"detail": "你不是当前房间成员。"}, status_code=403)
    if str(room.get("status", "")).strip().lower() != "active":
        return JSONResponse({"detail": "当前房间不在决策阶段。"}, status_code=409)
    simulator = _simulator("real-original")
    team_id = assignment["team_id"]
    context = _room_team_context(simulator, room, team_id)
    raw_payload = _room_player_submission_payload(simulator, context, {key: value for key, value in payload.items()})
    decision = simulator._build_simulation_input(str(room.get("current_round", "r1")), raw_payload, context=context, headcount_is_delta=True)
    report = simulator.simulate_room_round(
        round_id=str(room.get("current_round", "r1")).strip().lower(),
        human_decisions_by_team={team_id: decision},
        human_team_ids=[team_id],
        team_states=_room_team_states(room),
        game_id=str(room.get("room_id", "")),
        mode="multiplayer-preview",
        participant_team_ids=_room_active_team_ids(room),
        current_home_city_by_team={team_id: _room_home_city_for_team(room, team_id)},
        use_historical_initial_state=False,
    )[0][team_id]
    return JSONResponse(_build_round_preview_payload(decision, report))


@app.post("/multi/rooms/{room_id}/submit", response_class=HTMLResponse)
async def multiplayer_room_submit(request: Request, room_id: str) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return _template_response(request, "home.html", {"history_error": str(exc)}, status_code=403)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return _template_response(request, "home.html", {"history_error": "未找到该多人房间。"}, status_code=404)
    room = _ensure_room_progress(room)
    if str(room.get("status", "")).strip().lower() != "active":
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    assignment = next((item for item in _room_assignments(room) if item["client_id"] == user["client_id"]), None)
    if assignment is None:
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    simulator = _simulator("real-original")
    team_id = assignment["team_id"]
    context = _room_team_context(simulator, room, team_id)
    form_payload = {key: value for key, value in form.items()}
    raw_payload = _room_player_submission_payload(simulator, context, form_payload)
    try:
        decision = simulator._build_simulation_input(str(room.get("current_round", "r1")), raw_payload, context=context, headcount_is_delta=True)
        errors = simulator._validate(decision, context)
        if errors:
            raise ValueError("\n".join(errors))
    except ValueError as exc:
        player = _room_player(room, user["client_id"]) or {}
        session = _player_room_session(room, player, team_id)
        return _template_response(
            request,
            "round.html",
            _build_round_page_context(
                simulator,
                session,
                error=str(exc),
                preserve_form=raw_payload,
                submit_action_url=str(request.url_for("multiplayer_room_submit", room_id=room_id)),
                preview_action_url=str(request.url_for("multiplayer_room_preview", room_id=room_id)),
                next_action_url=str(request.url_for("multiplayer_room_next", room_id=room_id)),
                multiplayer_room_snapshot=_room_snapshot(room, current_client_id=user["client_id"]),
            ),
            status_code=422,
        )
    updated_room = dict(room)
    pending_submissions = dict(updated_room.get("pending_submissions", {})) if isinstance(updated_room.get("pending_submissions"), dict) else {}
    current_round = str(updated_room.get("current_round", "r1")).strip().lower()
    current_round_submissions = dict(pending_submissions.get(current_round, {})) if isinstance(pending_submissions.get(current_round, {}), dict) else {}
    current_round_submissions[team_id] = raw_payload
    pending_submissions[current_round] = current_round_submissions
    updated_room["pending_submissions"] = pending_submissions
    updated_room = multiplayer_room_store.save_room(updated_room)
    updated_room = _ensure_room_progress(updated_room)
    if str(updated_room.get("status", "")).strip().lower() in {"report", "finished"}:
        player = _room_player(updated_room, user["client_id"]) or {}
        latest_report = player.get("latest_report_detail")
        if isinstance(latest_report, dict):
            return RedirectResponse(url=str(request.url_for("multiplayer_room_report", room_id=room_id)), status_code=303)
    return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)


@app.get("/multi/rooms/{room_id}/report", response_class=HTMLResponse)
async def multiplayer_room_report(request: Request, room_id: str) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return _template_response(request, "home.html", {"history_error": "未找到该多人房间。"}, status_code=404)
    player = _room_player(room, user["client_id"])
    assignment = next((item for item in _room_assignments(room) if item["client_id"] == user["client_id"]), None)
    if not isinstance(player, dict) or assignment is None:
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    latest_report = player.get("latest_report_detail")
    if not isinstance(latest_report, dict):
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    session = _player_room_session(room, player, assignment["team_id"])
    simulator = _simulator("real-original")
    report_context = _build_report_page_context(
        simulator,
        session,
        latest_report,
        cache_owner_client_id=str(user["client_id"]),
        next_action_url=str(request.url_for("multiplayer_room_next", room_id=room_id)),
        next_action_label=("查看最终总结" if str(room.get("status", "")).strip().lower() == "finished" else "进入下一轮决策"),
    )
    return _template_response(request, "report.html", report_context)


@app.post("/multi/rooms/{room_id}/next", response_class=HTMLResponse)
async def multiplayer_room_next(request: Request, room_id: str) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return _template_response(request, "home.html", {"history_error": str(exc)}, status_code=403)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return _template_response(request, "home.html", {"history_error": "未找到该多人房间。"}, status_code=404)
    if str(room.get("status", "")).strip().lower() == "finished":
        return RedirectResponse(url=str(request.url_for("multiplayer_room_final", room_id=room_id)), status_code=303)
    if str(room.get("status", "")).strip().lower() != "report":
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    simulator = _simulator("real-original")
    next_round_id = _next_round(simulator, str(room.get("latest_round_id", room.get("current_round", "r1"))))
    if next_round_id is None:
        updated_room = dict(room)
        updated_room["status"] = "finished"
        multiplayer_room_store.save_room(updated_room)
        return RedirectResponse(url=str(request.url_for("multiplayer_room_final", room_id=room_id)), status_code=303)
    updated_room = dict(room)
    updated_room["status"] = "active"
    updated_room["current_round"] = next_round_id
    round_started_map = dict(updated_room.get("round_started_at_ms_by_round", {})) if isinstance(updated_room.get("round_started_at_ms_by_round"), dict) else {}
    round_started_map[next_round_id] = int(time.time() * 1000)
    updated_room["round_started_at_ms_by_round"] = round_started_map
    updated_room["pending_submissions"] = {}
    updated_room = multiplayer_room_store.save_room(updated_room)
    return RedirectResponse(url=str(request.url_for("multiplayer_room_game", room_id=room_id)), status_code=303)


@app.get("/multi/rooms/{room_id}/final", response_class=HTMLResponse)
async def multiplayer_room_final(request: Request, room_id: str) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    room = multiplayer_room_store.get_room_raw(room_id)
    if room is None:
        return _template_response(request, "home.html", {"history_error": "未找到该多人房间。"}, status_code=404)
    player = _room_player(room, user["client_id"])
    assignment = next((item for item in _room_assignments(room) if item["client_id"] == user["client_id"]), None)
    if not isinstance(player, dict) or assignment is None:
        return RedirectResponse(url=str(request.url_for("multiplayer_room_page", room_id=room_id)), status_code=303)
    session = _player_room_session(room, player, assignment["team_id"])
    return _template_response(request, "final.html", _build_final_page_context_from_session(session))


def _render_single_setup(
    request: Request,
    simulator: ExschoolSimulator,
    *,
    mode: str,
    error: str | None = None,
    form_values: dict[str, object] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return _template_response(
        request,
        "setup.html",
        {
            "key_data": simulator.key_data,
            "equation_rows": _equation_rows(simulator.key_data),
            "home_city_options": HOME_CITY_OPTIONS,
            "home_city_labels": CITY_LABELS,
            "single_player_mode": mode,
            "single_player_mode_label": _single_player_mode_label(mode),
            "error": error,
            "setup_values": {
                "company_name": str((form_values or {}).get("company_name", "") or ""),
                "home_city": str((form_values or {}).get("home_city", HOME_CITY_OPTIONS[0]) or HOME_CITY_OPTIONS[0]),
            },
        },
        status_code=status_code,
    )


@app.get("/single/setup", response_class=HTMLResponse)
async def single_setup(request: Request, mode: str = SINGLE_PLAYER_MODE_DEFAULT) -> HTMLResponse:
    if not _current_user(request):
        return _auth_redirect(request)
    normalized_mode = _normalize_single_player_mode(mode)
    simulator = _simulator(normalized_mode)
    request.session["pending_single_player_mode"] = normalized_mode
    return _render_single_setup(request, simulator, mode=normalized_mode)


@app.get("/single-fixed/setup", response_class=HTMLResponse)
async def single_fixed_setup(request: Request) -> HTMLResponse:
    return await single_setup(request, mode="real-original")


@app.get("/single-real/setup", response_class=HTMLResponse)
async def single_real_setup(request: Request) -> HTMLResponse:
    return await single_setup(request, mode="real-original")


async def _single_start_impl(request: Request, *, mode_override: str | None = None) -> HTMLResponse:
    if not _current_user(request):
        return _auth_redirect(request)
    form = await request.form()
    company_name = str(form.get("company_name", "")).strip()
    home_city = str(form.get("home_city", "")).strip()
    setup_form_values = {
        "company_name": company_name,
        "home_city": home_city or HOME_CITY_OPTIONS[0],
    }
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        fallback_mode = _normalize_single_player_mode(
            mode_override or form.get("single_player_mode") or request.session.get("pending_single_player_mode")
        )
        return _render_single_setup(
            request,
            _simulator(fallback_mode),
            mode=fallback_mode,
            error=str(exc),
            form_values=setup_form_values,
            status_code=403,
        )
    pending_mode = request.session.get("pending_single_player_mode")
    explicit_mode = str(mode_override or form.get("single_player_mode") or request.query_params.get("mode") or "").strip()
    if mode_override is None and not explicit_mode:
        fallback_mode = _normalize_single_player_mode(pending_mode or SINGLE_PLAYER_MODE_DEFAULT)
        return _render_single_setup(
            request,
            _simulator(fallback_mode),
            mode=fallback_mode,
            error="模式信息缺失，请重新从模式选择页开始。",
            form_values=setup_form_values,
            status_code=422,
        )
    single_player_mode = _normalize_single_player_mode(
        mode_override
        or form.get("single_player_mode")
        or request.query_params.get("mode")
        or pending_mode
        or SINGLE_PLAYER_MODE_DEFAULT
    )
    simulator = _simulator(single_player_mode)

    if not company_name:
        return _render_single_setup(
            request,
            simulator,
            mode=single_player_mode,
            error="请输入公司名称。",
            form_values=setup_form_values,
            status_code=422,
        )
    if home_city not in HOME_CITY_OPTIONS:
        return _render_single_setup(
            request,
            simulator,
            mode=single_player_mode,
            error="请选择有效的主场城市。",
            form_values=setup_form_values,
            status_code=422,
        )

    session = _default_session(simulator)
    session["company_name"] = company_name
    session["home_city"] = home_city
    session["single_player_mode"] = single_player_mode
    session["started"] = True
    _set_terminal_game_session(request, None)
    request.session.pop("pending_single_player_mode", None)
    _save_game_session(request, session)
    return _template_response(request, "round.html", _build_round_page_context(simulator, session))


@app.post("/single/start", response_class=HTMLResponse)
async def single_start(request: Request) -> HTMLResponse:
    return await _single_start_impl(request)


@app.post("/single-fixed/start", response_class=HTMLResponse)
async def single_fixed_start(request: Request) -> HTMLResponse:
    return await _single_start_impl(request, mode_override="real-original")


@app.post("/single-real/start", response_class=HTMLResponse)
async def single_real_start(request: Request) -> HTMLResponse:
    return await _single_start_impl(request, mode_override="real-original")


@app.get("/game/select/{game_id}")
async def game_select(request: Request, game_id: str) -> RedirectResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    session = user_game_store.get_active_game_session(user["client_id"], game_id=game_id)
    if session is None:
        return RedirectResponse(url=str(request.url_for("home")), status_code=303)
    _set_selected_game_id(request, str(session.get("game_id", "")).strip() or None)
    _set_terminal_game_session(request, None)
    return RedirectResponse(url=str(request.url_for("game_round")), status_code=303)


@app.post("/game/delete/{game_id}")
async def delete_saved_game(request: Request, game_id: str) -> Response:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return Response(content=str(exc), status_code=403)
    user_game_store.clear_active_game_session(user["client_id"], game_id=game_id)
    fallback_session = user_game_store.get_active_game_session(user["client_id"])
    _set_selected_game_id(request, str(fallback_session.get("game_id", "")).strip() if fallback_session else None)
    return RedirectResponse(url=str(request.url_for("home")), status_code=303)


@app.get("/history/{game_id}", response_class=HTMLResponse)
async def history_detail(request: Request, game_id: str) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    history_item = user_game_store.get_history_game(user["client_id"], game_id)
    if history_item is None:
        return _template_response(request, "home.html", {"history_error": "未找到该历史对局。"}, status_code=404)
    reports = _reports_from_session(history_item.get("reports"))
    all_company_rounds = _all_company_rounds_from_session(history_item.get("all_company_rounds"))
    context = _build_final_page_context(
        company_name=str(history_item.get("company_name", "")),
        home_city=str(history_item.get("home_city", "")),
        single_player_mode=str(history_item.get("single_player_mode", "")),
        reports=reports,
        all_company_rounds=all_company_rounds,
    )
    context["history_view"] = True
    return _template_response(request, "final.html", context)


@app.get("/game", response_class=HTMLResponse)
async def game_round(request: Request) -> HTMLResponse:
    if not _current_user(request):
        return _auth_redirect(request)
    bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    requested_game_id = _normalize_game_id(request.query_params.get("game_id"))
    try:
        session = _get_game_session(
            request,
            bootstrap_simulator,
            requested_game_id=requested_game_id,
            persist_selected_game_id=requested_game_id is None,
        )
    except RequestedGameNotFoundError as exc:
        return _template_response(request, "home.html", {"history_error": str(exc)}, status_code=404)
    if session.get("terminal"):
        return _template_response(request, "final.html", _build_final_page_context_from_session(session))
    if requested_game_id is None and not session.get("started"):
        terminal_session = _terminal_game_session(request)
        if terminal_session and terminal_session.get("terminal"):
            return _template_response(request, "final.html", _build_final_page_context_from_session(terminal_session))
    simulator = _simulator(_single_player_mode_from_session(session))
    if not session.get("started"):
        return _render_single_setup(
            request,
            simulator,
            mode=_single_player_mode_from_session(session),
        )
    current_round = str(session.get("current_round", "")).strip().lower()
    latest_report = _latest_report_detail_from_session(session)
    if current_round and current_round in _submitted_round_ids(session) and latest_report:
        latest_round = str(latest_report.get("round_id", "")).strip().lower()
        if latest_round == current_round:
            report_context = _build_report_page_context(
                simulator,
                session,
                latest_report,
                cache_owner_client_id=str(_current_user(request)["client_id"]),
            )
            _save_game_session(request, session)
            return _template_response(
                request,
                "report.html",
                report_context,
            )
    return _template_response(request, "round.html", _build_round_page_context(simulator, session))


@app.post("/game/submit", response_class=HTMLResponse)
async def submit_round(request: Request) -> HTMLResponse:
    user = _current_user(request)
    if not user:
        return _auth_redirect(request)
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
        try:
            session = _get_game_session(
                request,
                bootstrap_simulator,
                requested_game_id=_normalize_game_id(form.get("game_id")),
            )
        except RequestedGameNotFoundError as missing_exc:
            return _template_response(request, "home.html", {"history_error": str(missing_exc)}, status_code=404)
        simulator = _simulator(_single_player_mode_from_session(session))
        return _template_response(
            request,
            "round.html",
            _build_round_page_context(simulator, session, error=str(exc)),
            status_code=403,
        )
    form_payload = {key: value for key, value in form.items()}
    bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    try:
        session = _get_game_session(
            request,
            bootstrap_simulator,
            requested_game_id=_normalize_game_id(form_payload.get("game_id")),
        )
    except RequestedGameNotFoundError as exc:
        return _template_response(request, "home.html", {"history_error": str(exc)}, status_code=404)
    simulator = _simulator(_single_player_mode_from_session(session))
    if not session.get("started"):
        return _template_response(request, "home.html", {}, status_code=400)
    current_round = str(session["current_round"]).strip().lower()
    if current_round in _submitted_round_ids(session):
        latest_report = _latest_report_detail_from_session(session)
        if latest_report:
            latest_round = str(latest_report.get("round_id", "")).strip().lower()
            if latest_round == current_round:
                report_context = _build_report_page_context(
                    simulator,
                    session,
                    latest_report,
                    cache_owner_client_id=str(user["client_id"]),
                )
                _save_game_session(request, session)
                return _template_response(
                    request,
                    "report.html",
                    report_context,
                )
        return _template_response(
            request,
            "round.html",
            _build_round_page_context(simulator, session, error=f"{current_round.upper()} 已提交，请直接进入下一轮。"),
            status_code=409,
        )
    try:
        _enforce_request_guard(
            scope="game_submit_user",
            identity=str(user.get("client_id", "")),
            limit=GAME_SUBMIT_RATE_LIMIT_USER_ATTEMPTS,
            window_seconds=GAME_SUBMIT_RATE_LIMIT_WINDOW_SECONDS,
            block_seconds=GAME_SUBMIT_RATE_LIMIT_WINDOW_SECONDS,
            message="提交过于频繁，请稍后再试。",
        )
        _enforce_request_guard(
            scope="game_submit_ip",
            identity=_client_ip(request),
            limit=GAME_SUBMIT_RATE_LIMIT_IP_ATTEMPTS,
            window_seconds=GAME_SUBMIT_RATE_LIMIT_WINDOW_SECONDS,
            block_seconds=GAME_SUBMIT_RATE_LIMIT_WINDOW_SECONDS,
            message="提交过于频繁，请稍后再试。",
        )
    except ValueError as exc:
        return _template_response(
            request,
            "round.html",
            _build_round_page_context(simulator, session, error=str(exc)),
            status_code=429,
        )
    submit_mode = str(form_payload.get("submit_mode", "")).strip().lower()
    deadline_ms = _current_round_deadline_ms(session)
    current_ms = int(time.time() * 1000)
    if deadline_ms is not None:
        if current_ms > deadline_ms + ROUND_TIMEOUT_AUTO_SUBMIT_GRACE_SECONDS * 1000:
            return _template_response(
                request,
                "round.html",
                _build_round_page_context(simulator, session, error=f"{current_round.upper()} 已超时，不能继续提交。"),
                status_code=409,
            )
        if current_ms > deadline_ms and submit_mode != "timeout-auto":
            return _template_response(
                request,
                "round.html",
                _build_round_page_context(simulator, session, error=f"{current_round.upper()} 已截止，请等待系统自动提交。"),
                status_code=409,
            )
    try:
        expected_round = str(session["current_round"])
        state = _state_from_session(session.get("campaign_state"))
        context = simulator._context_with_campaign_state(
            expected_round,
            state,
            current_home_city=str(session.get("home_city", "")),
        )
        context["game_id"] = str(session.get("game_id", "default"))
        context["single_player_mode"] = _single_player_mode_from_session(session)
        decision = simulator.parse_form(form_payload, context)
        if decision.round_id != expected_round:
            raise GameFlowError("当前提交的轮次顺序不正确。")
        report = simulator._simulate_with_context(decision, context, mode="campaign")
        next_state = simulator._next_campaign_state(report, decision, state)
    except ValueError as exc:
        preserve_form = simulator.stateful_default_payload(
            str(session["current_round"]),
            _state_from_session(session.get("campaign_state")),
            current_home_city=str(session.get("home_city", "")),
        )
        for market in preserve_form["markets"]:
            slug = market.lower()
            preserve_form["markets"][market]["subscribed_market_report"] = form_payload.get(f"{slug}_market_report") == "1"
            if f"{slug}_agent_change" in form_payload:
                preserve_form["markets"][market]["agent_change"] = _preserve_numeric_form_value(
                    form_payload[f"{slug}_agent_change"],
                    integer=True,
                )
            if f"{slug}_marketing_investment" in form_payload:
                preserve_form["markets"][market]["marketing_investment"] = _preserve_numeric_form_value(
                    form_payload[f"{slug}_marketing_investment"]
                )
            if f"{slug}_price" in form_payload:
                preserve_form["markets"][market]["price"] = _preserve_numeric_form_value(form_payload[f"{slug}_price"])
        for field in [
            "loan_delta",
            "workers",
            "engineers",
            "worker_salary",
            "engineer_salary",
            "management_investment",
            "quality_investment",
            "research_investment",
            "products_planned",
        ]:
            if field in form_payload:
                preserve_form[field] = _preserve_numeric_form_value(
                    form_payload[field],
                    integer=field in {"workers", "engineers", "products_planned"},
                )
        preserve_form["round_id"] = str(session["current_round"])
        return _template_response(
            request,
            "round.html",
            _build_round_page_context(simulator, session, error=str(exc), preserve_form=preserve_form),
            status_code=422,
        )

    report_round_id = str(report["round_id"]).strip().lower()
    reports = [
        existing
        for existing in _reports_from_session(session.get("reports"))
        if str(existing.get("round_id", "")).strip().lower() != report_round_id
    ]
    reports.append(_report_summary(report))
    session["reports"] = _reports_to_session(reports)
    session["latest_report_detail"] = report
    _remember_report_detail(session, report)
    all_company_rounds = [
        existing
        for existing in _all_company_rounds_from_session(session.get("all_company_rounds"))
        if str(existing.get("round_id", "")).strip().lower() != report_round_id
    ]
    all_company_rounds.append(_compact_all_company_round_point(report))
    session["all_company_rounds"] = _all_company_rounds_to_session(all_company_rounds)
    session["campaign_state"] = _state_to_session(next_state)
    _save_game_session(request, session)
    report_context = _build_report_page_context(simulator, session, report, cache_owner_client_id=str(user["client_id"]))
    _save_game_session(request, session)

    return _template_response(
        request,
        "report.html",
        report_context,
    )


@app.post("/game/next", response_class=HTMLResponse)
async def next_round(request: Request) -> HTMLResponse:
    if not _current_user(request):
        return _auth_redirect(request)
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
        try:
            session = _get_game_session(
                request,
                bootstrap_simulator,
                requested_game_id=_normalize_game_id(form.get("game_id")),
            )
        except RequestedGameNotFoundError as missing_exc:
            return _template_response(request, "home.html", {"history_error": str(missing_exc)}, status_code=404)
        simulator = _simulator(_single_player_mode_from_session(session))
        return _template_response(
            request,
            "round.html",
            _build_round_page_context(simulator, session, error=str(exc)),
            status_code=403,
        )
    bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    try:
        session = _get_game_session(
            request,
            bootstrap_simulator,
            requested_game_id=_normalize_game_id(form.get("game_id")),
        )
    except RequestedGameNotFoundError as exc:
        return _template_response(request, "home.html", {"history_error": str(exc)}, status_code=404)
    simulator = _simulator(_single_player_mode_from_session(session))
    if not session.get("started"):
        return _template_response(request, "home.html", {}, status_code=400)

    current_round = str(session["current_round"])
    reports = _reports_from_session(session.get("reports"))
    completed_rounds = len(reports)
    current_round_index = simulator.available_rounds().index(current_round)

    if completed_rounds <= current_round_index:
        return _template_response(
            request,
            "round.html",
            _build_round_page_context(simulator, session, error="请先提交当前轮次，再进入下一轮。"),
            status_code=400,
        )

    next_round_id = _next_round(simulator, current_round)
    if next_round_id is None:
        session["terminal"] = True
        final_context = _build_final_page_context_from_session(session)
        if not session.get("archived"):
            user = _current_user(request)
            if user:
                user_game_store.archive_completed_game(user, session, reports)
            session["archived"] = True
        _save_game_session(request, session)
        return _template_response(request, "final.html", final_context)

    session["current_round"] = next_round_id
    _save_game_session(request, session)
    return _template_response(request, "round.html", _build_round_page_context(simulator, session))


@app.post("/game/report-image")
async def report_image(request: Request) -> Response:
    user = _current_user(request)
    if not user:
        return Response(status_code=401)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "detail": "请求体不是有效的 JSON。"}, status_code=400)
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "detail": "请求体必须是 JSON 对象。"}, status_code=400)
    try:
        _require_csrf_token(request, payload.get("_csrf") or payload.get("csrf_token") or request.headers.get("x-csrf-token"))
    except CsrfError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=403)
    try:
        _enforce_request_guard(
            scope="report_image_user",
            identity=str(user.get("client_id", "")),
            limit=REPORT_IMAGE_RATE_LIMIT_USER_ATTEMPTS,
            window_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
            block_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
            message="财报导出过于频繁，请稍后再试。",
        )
        _enforce_request_guard(
            scope="report_image_ip",
            identity=_client_ip(request),
            limit=REPORT_IMAGE_RATE_LIMIT_IP_ATTEMPTS,
            window_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
            block_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
            message="财报导出过于频繁，请稍后再试。",
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=429)
    cache_key = str(payload.get("cache_key", "")).strip().lower()
    if not cache_key or any(ch not in "0123456789abcdef" for ch in cache_key) or len(cache_key) != 64:
        return Response(status_code=400)
    bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    requested_game_id = _normalize_game_id(payload.get("game_id"))
    try:
        session = _get_game_session(
            request,
            bootstrap_simulator,
            requested_game_id=requested_game_id,
        )
        simulator = _simulator(_single_player_mode_from_session(session))
        latest_report = _latest_report_detail_from_session(session)
    except RequestedGameNotFoundError:
        multiplayer_report_session = _multiplayer_report_session_for_game_id(requested_game_id, user)
        if multiplayer_report_session is None:
            return Response(status_code=404)
        session, latest_report, simulator = multiplayer_report_session
    if not session.get("started"):
        return Response(status_code=404)
    if latest_report is None:
        return Response(status_code=404)
    request_round_id = str(payload.get("round_id", "")).strip().lower()
    target_report = latest_report
    if request_round_id:
        if str(latest_report.get("round_id", "")).strip().lower() != request_round_id:
            return Response(status_code=409)
    html, expected_cache_key = _report_export_artifacts(simulator, session, target_report)
    if cache_key != expected_cache_key and not request_round_id:
        historical_report = _report_detail_for_cache_key(session, cache_key) if ":" not in str(requested_game_id or "") else None
        if historical_report is not None:
            target_report = historical_report
            html, expected_cache_key = _report_export_artifacts(simulator, session, target_report)
    if cache_key != expected_cache_key:
        return Response(status_code=409)
    _prune_report_image_cache()
    cached_png_path = _report_image_cache_path_from_key(cache_key, client_id=str(user["client_id"]))
    if cached_png_path.exists():
        return Response(content=cached_png_path.read_bytes(), media_type="image/png")
    png_bytes = _render_report_image_bytes(html)
    cached_png_path.write_bytes(png_bytes)
    return Response(content=png_bytes, media_type="image/png")


@app.post("/game/preview")
async def preview_round(request: Request) -> JSONResponse:
    user = _current_user(request)
    if not user:
        return JSONResponse({"ok": False, "detail": "请先登录。"}, status_code=401)
    form = await request.form()
    try:
        _require_csrf_token(request, form.get("_csrf"))
    except CsrfError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=403)
    form_payload = {key: value for key, value in form.items()}
    bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    try:
        session = _get_game_session(
            request,
            bootstrap_simulator,
            requested_game_id=_normalize_game_id(form_payload.get("game_id")),
        )
    except RequestedGameNotFoundError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=404)
    simulator = _simulator(_single_player_mode_from_session(session))
    if not session.get("started"):
        return JSONResponse({"ok": False, "detail": "当前没有进行中的对局。"}, status_code=400)
    current_round = str(session["current_round"]).strip().lower()
    if current_round in _submitted_round_ids(session):
        return JSONResponse({"ok": False, "detail": f"{current_round.upper()} 已提交，请直接进入下一轮。"}, status_code=409)
    deadline_ms = _current_round_deadline_ms(session)
    current_ms = int(time.time() * 1000)
    if deadline_ms is not None:
        if current_ms > deadline_ms + ROUND_TIMEOUT_AUTO_SUBMIT_GRACE_SECONDS * 1000:
            return JSONResponse({"ok": False, "detail": f"{current_round.upper()} 已超时，不能继续试算。"}, status_code=409)
        if current_ms > deadline_ms:
            return JSONResponse({"ok": False, "detail": f"{current_round.upper()} 已截止，请等待系统自动提交。"}, status_code=409)
    try:
        expected_round = str(session["current_round"])
        state = _state_from_session(session.get("campaign_state"))
        context = simulator._context_with_campaign_state(
            expected_round,
            state,
            current_home_city=str(session.get("home_city", "")),
        )
        context["game_id"] = str(session.get("game_id", "default"))
        context["single_player_mode"] = _single_player_mode_from_session(session)
        decision = simulator.parse_form(form_payload, context)
        if decision.round_id != expected_round:
            raise GameFlowError("当前提交的轮次顺序不正确。")
        report = simulator._simulate_with_context(decision, context, mode="campaign")
    except ValueError as exc:
        return JSONResponse({"ok": False, "detail": str(exc)}, status_code=422)
    return JSONResponse({"ok": True, "preview": _build_round_preview_payload(decision, report)})


@app.get("/game/report-image/{cache_key}")
async def report_image_cached(request: Request, cache_key: str) -> Response:
    user = _current_user(request)
    if not user:
        return Response(status_code=401)
    if not cache_key or any(ch not in "0123456789abcdef" for ch in cache_key.lower()) or len(cache_key) != 64:
        return Response(status_code=400)
    download_requested = str(request.query_params.get("download", "")).strip().lower() in {"1", "true", "yes", "on"}
    bootstrap_simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    requested_game_id = _normalize_game_id(request.query_params.get("game_id"))
    try:
        session = _get_game_session(
            request,
            bootstrap_simulator,
            requested_game_id=requested_game_id,
            persist_selected_game_id=requested_game_id is None,
        )
        simulator = _simulator(_single_player_mode_from_session(session))
        latest_report = _latest_report_detail_from_session(session)
    except RequestedGameNotFoundError:
        multiplayer_report_session = _multiplayer_report_session_for_game_id(requested_game_id, user)
        if multiplayer_report_session is None:
            return Response(status_code=204)
        session, latest_report, simulator = multiplayer_report_session
    if not session.get("started"):
        return Response(status_code=204)
    normalized_cache_key = cache_key.lower()
    cached_png_path = _report_image_cache_path_from_key(normalized_cache_key, client_id=str(user["client_id"]))
    is_multiplayer_report = ":" in str(requested_game_id or "")
    known_cache_key_for_session = (
        _report_image_cache_key_belongs_to_session(session, normalized_cache_key) if not is_multiplayer_report else False
    )
    historical_report = _report_detail_for_cache_key(session, normalized_cache_key) if known_cache_key_for_session else None
    headers: dict[str, str] = {}
    if download_requested:
        headers["Content-Disposition"] = 'attachment; filename="report.png"'
    if latest_report is None:
        if known_cache_key_for_session and cached_png_path.exists():
            return Response(content=cached_png_path.read_bytes(), media_type="image/png", headers=headers)
        return Response(status_code=204)
    target_report = latest_report
    _html, expected_cache_key = _report_export_artifacts(simulator, session, target_report)
    if normalized_cache_key != expected_cache_key:
        if known_cache_key_for_session and cached_png_path.exists():
            return Response(content=cached_png_path.read_bytes(), media_type="image/png", headers=headers)
        if historical_report is None:
            return Response(status_code=204)
        target_report = historical_report
        _html, expected_cache_key = _report_export_artifacts(simulator, session, target_report)
        if normalized_cache_key != expected_cache_key:
            return Response(status_code=204)
    if not cached_png_path.exists():
        if not download_requested:
            return Response(status_code=204)
        try:
            _enforce_request_guard(
                scope="report_image_user",
                identity=str(user.get("client_id", "")),
                limit=REPORT_IMAGE_RATE_LIMIT_USER_ATTEMPTS,
                window_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
                block_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
                message="财报导出过于频繁，请稍后再试。",
            )
            _enforce_request_guard(
                scope="report_image_ip",
                identity=_client_ip(request),
                limit=REPORT_IMAGE_RATE_LIMIT_IP_ATTEMPTS,
                window_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
                block_seconds=REPORT_IMAGE_RATE_LIMIT_WINDOW_SECONDS,
                message="财报导出过于频繁，请稍后再试。",
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "detail": str(exc)}, status_code=429)
        html, _expected_cache_key = _report_export_artifacts(simulator, session, target_report)
        _prune_report_image_cache()
        png_bytes = _render_report_image_bytes(html)
        cached_png_path.write_bytes(png_bytes)
    return Response(content=cached_png_path.read_bytes(), media_type="image/png", headers=headers)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True})


@app.get("/favicon.ico")
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/rounds/{round_id}/defaults")
async def round_defaults(round_id: str) -> JSONResponse:
    simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    if round_id not in simulator.available_rounds():
        return JSONResponse({"ok": False, "detail": f"未知轮次：{round_id}"}, status_code=404)
    return JSONResponse(simulator.default_payload(round_id))


@app.get("/api/campaign/defaults")
async def campaign_defaults() -> JSONResponse:
    simulator = _simulator(SINGLE_PLAYER_MODE_DEFAULT)
    return JSONResponse(simulator.campaign_default_payload())


if __name__ == "__main__":
    uvicorn.run(
        "exschool_game.app:app",
        host=os.environ.get("EXSCHOOL_HOST", "127.0.0.1"),
        port=int(os.environ.get("EXSCHOOL_PORT", "8010")),
        reload=os.environ.get("EXSCHOOL_RELOAD", "0") == "1",
        root_path=APP_ROOT_PATH,
        proxy_headers=True,
    )
