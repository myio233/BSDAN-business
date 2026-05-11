from __future__ import annotations

import argparse
import contextlib
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from exschool_game.auth_store import auth_store
from exschool_game.multiplayer_store import MULTIPLAYER_ROOM_STORE_PATH


PASSWORD = "playwright-pass"
PAGE_TIMEOUT_MS = 90_000
MODE_LABEL = "真实原版竞争"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate multiplayer browser flow with real Playwright user actions.")
    parser.add_argument("--human-seats", type=int, default=2, help="Total human players including host.")
    parser.add_argument("--bot-count", type=int, default=1, help="Bot seats to fill after human seats.")
    parser.add_argument("--rounds", type=int, default=1, help="Rounds to validate end-to-end.")
    parser.add_argument("--base-url", type=str, default="", help="Use an existing deployed server instead of starting a local one.")
    parser.add_argument(
        "--account-email",
        action="append",
        default=[],
        help="Existing login email for --base-url mode. Pass once per human seat in host-first order.",
    )
    parser.add_argument("--password", type=str, default=PASSWORD, help="Password for all browser accounts.")
    parser.add_argument("--guest-home-city-label", type=str, default="成都", help="Home-city label to pick for the first guest seat.")
    parser.add_argument(
        "--guest-home-city-loan-limit",
        type=str,
        default="¥3,500,000",
        help="Expected loan-limit text on the first guest round page after changing home city.",
    )
    parser.add_argument("--check-download", action="store_true", help="Verify that report image download produces a PNG file.")
    parser.add_argument("--headful", action="store_true", help="Run Chromium with a visible window.")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=ROOT_DIR / "outputs" / "playwright_multiplayer_validation",
        help="Directory to write screenshots and summary artifacts.",
    )
    return parser.parse_args()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, timeout_seconds: float = 20.0) -> None:
    import urllib.request

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=1.0) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("local multiplayer validation server did not become ready")


def _seed_user(prefix: str, index: int) -> tuple[str, str, str]:
    unique = str(time.time_ns())
    email = f"{prefix}-{index}-{unique}@example.com"
    name = f"{prefix}-{index}-{unique}"
    user, _token = auth_store.register_user(name, email, PASSWORD)
    return email, name, str(user["client_id"])


def _existing_user(email: str) -> tuple[str, str, str]:
    user = auth_store.get_public_by_email(email)
    if user is None:
        raise RuntimeError(f"existing auth account not found for {email!r}")
    return email, str(user["name"]), str(user["client_id"])


def _login(page: Page, base_url: str, email: str, password: str) -> None:
    page.goto(f"{base_url}/auth?mode=login", wait_until="load")
    page.locator('input[name="account"]').fill(email)
    page.locator('input[name="password"]').fill(password)
    page.get_by_role("button", name="登录").click()
    page.wait_for_url(f"{base_url}/", wait_until="load")


def _wait_until(label: str, predicate, timeout_seconds: float = 30.0, interval_seconds: float = 0.25) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # pragma: no cover - retry helper
            last_error = exc
        time.sleep(interval_seconds)
    if last_error is not None:
        raise RuntimeError(f"{label} not reached: {last_error}") from last_error
    raise RuntimeError(f"{label} not reached before timeout")


def _wait_for_text(page: Page, selector: str, expected_text: str, timeout_seconds: float = 30.0) -> None:
    def _matches() -> bool:
        content = (page.locator(selector).text_content() or "").strip()
        return content == expected_text

    _wait_until(f"{selector} == {expected_text!r}", _matches, timeout_seconds=timeout_seconds)


def _wait_for_contains(page: Page, selector: str, expected_fragment: str, timeout_seconds: float = 30.0) -> None:
    def _matches() -> bool:
        content = (page.locator(selector).text_content() or "").strip()
        return expected_fragment in content

    _wait_until(f"{selector} contains {expected_fragment!r}", _matches, timeout_seconds=timeout_seconds)


def _wait_for_url_suffix(page: Page, suffix: str, timeout_seconds: float = 30.0) -> None:
    _wait_until(f"url endswith {suffix!r}", lambda: page.url.endswith(suffix), timeout_seconds=timeout_seconds)


def _verify_home_and_setup_entry(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/", wait_until="load")
    page.get_by_role("link", name="多人房间").wait_for()
    page.get_by_role("link", name="重新开一局").click()
    page.wait_for_url(f"{base_url}/mode", wait_until="load")
    page.get_by_text("选择这一局的游戏模式。", exact=True).wait_for()
    page.locator(".mode-card-multiplayer").wait_for()
    page.get_by_role("link", name="创建多人房间").click()
    page.wait_for_url(f"{base_url}/multi/setup", wait_until="load")
    page.get_by_role("heading", name="创建或加入实时多人房间。").wait_for()


def _create_room(
    page: Page,
    base_url: str,
    *,
    human_seats: int,
    bot_count: int,
    setup_screenshot_path: Path | None = None,
) -> tuple[str, str]:
    _verify_home_and_setup_entry(page, base_url)
    if setup_screenshot_path is not None:
        _capture(page, setup_screenshot_path)
    page.locator('input[name="seat_limit"]').fill(str(human_seats))
    page.locator('input[name="bot_count"]').fill(str(bot_count))
    page.get_by_role("button", name="创建房间").click()
    page.wait_for_url(f"{base_url}/multi/rooms/*", wait_until="load")
    room_code_text = (page.locator("#multiplayer-room-code").text_content() or "").strip()
    room_code = room_code_text.replace("房间码", "", 1).strip()
    if not room_code:
        raise RuntimeError(f"room code missing after room creation: {room_code_text!r}")
    return page.url.rstrip("/"), room_code


def _refresh_room(page: Page) -> None:
    page.locator("#multiplayer-refresh-button").click()


def _join_room(page: Page, room_url: str) -> None:
    page.goto(room_url, wait_until="load")
    page.locator("#multiplayer-join-button").wait_for()
    page.locator("#multiplayer-join-button").click()
    _wait_for_contains(page, "#multiplayer-current-player-membership", "已加入")
    _wait_for_text(page, "#multiplayer-join-button", "已加入房间")


def _join_room_by_code(page: Page, base_url: str, room_code: str) -> None:
    page.goto(f"{base_url}/multi/setup", wait_until="load")
    page.locator("#multiplayer-room-code-input").fill(room_code)
    page.locator("[data-testid='multiplayer-join-by-code-submit']").click()
    page.wait_for_url(re.compile(rf"{re.escape(base_url)}/multi/rooms/[^/]+$"), wait_until="load")
    _wait_for_contains(page, "#multiplayer-current-player-membership", "已加入")
    _wait_for_contains(page, "#multiplayer-room-code", room_code)


def _mark_ready(page: Page) -> None:
    page.locator("#multiplayer-ready-button").wait_for()
    page.locator("#multiplayer-ready-button").click()
    _wait_for_text(page, "#multiplayer-ready-button", "取消准备")


def _set_home_city(page: Page, city_label: str) -> None:
    page.locator("#multiplayer-home-city-select").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    page.locator("#multiplayer-home-city-select").select_option(label=city_label)
    page.locator("#multiplayer-home-city-save").click()
    _wait_for_contains(page, "#multiplayer-home-city-copy", city_label)


def _start_room(page: Page, room_url: str) -> None:
    page.goto(room_url, wait_until="load")
    _wait_until(
        "host start button enabled",
        lambda: page.locator("#multiplayer-start-button").is_enabled(),
        timeout_seconds=40.0,
    )
    page.locator("#multiplayer-start-button").click()
    _wait_until(
        "multiplayer enter game link visible",
        lambda: page.locator("#multiplayer-enter-game-link").count() > 0
        and page.locator("#multiplayer-enter-game-link").is_visible(),
        timeout_seconds=40.0,
    )


def _assert_room_snapshot(page: Page, *, expected_occupied: int, expected_total: int, expected_ready: int, expected_bots: int) -> None:
    _wait_for_text(page, "#multiplayer-seat-summary", f"{expected_occupied} / {expected_total}")
    _wait_for_text(page, "#multiplayer-ready-summary", str(expected_ready))
    _wait_for_text(page, "#multiplayer-bot-summary", str(expected_bots))
    expected_human_cards = expected_total - expected_bots
    _wait_until(
        "seat cards rendered",
        lambda: page.locator("[data-testid^='multiplayer-seat-card-']").count() >= expected_human_cards,
        timeout_seconds=30.0,
    )
    _wait_until(
        "bot cards rendered",
        lambda: page.locator("[data-testid^='multiplayer-bot-seat-']").count() == expected_bots,
        timeout_seconds=30.0,
    )


def _open_current_round(page: Page, room_url: str, round_number: int, human_seats: int) -> None:
    page.goto(f"{room_url}/game", wait_until="load")
    page.locator("#decision-form").wait_for()
    page.locator("#open-submit-preview").wait_for()
    heading = (page.locator("h1").text_content() or "").upper()
    if f"R{round_number}" not in heading:
        raise RuntimeError(f"round heading missing expected round marker R{round_number}: {heading!r}")
    mode_chip = (page.locator("[data-testid='mode-chip']").text_content() or "").strip()
    if mode_chip != MODE_LABEL:
        raise RuntimeError(f"unexpected mode chip on multiplayer round page: {mode_chip!r}")
    _wait_until(
        "multiplayer live panel rendered",
        lambda: page.locator("[data-testid^='multiplayer-player-status-']").count() >= human_seats,
        timeout_seconds=30.0,
    )
    _wait_until(
        "round defaults loaded",
        lambda: (page.locator('input[name="products_planned"]').input_value() or "").strip() != ""
        and (page.locator('input[name="worker_salary"]').input_value() or "").strip() != ""
        and (page.locator('input[name="chengdu_price"]').input_value() or "").strip() != "",
        timeout_seconds=30.0,
    )
    round_page_data = json.loads((page.locator("#round-page-data").text_content() or "").strip())
    if round_number == 1:
        if int(round_page_data.get("currentWorkers", -1)) != 0:
            raise RuntimeError(f"unexpected multiplayer round worker baseline: {round_page_data.get('currentWorkers')!r}")
        if int(round_page_data.get("currentEngineers", -1)) != 0:
            raise RuntimeError(f"unexpected multiplayer round engineer baseline: {round_page_data.get('currentEngineers')!r}")


def _assert_round_contains_home_city(page: Page, home_city_label: str, loan_limit_text: str) -> None:
    body_text = (page.locator("body").inner_text() or "").strip()
    if home_city_label not in body_text:
        raise RuntimeError(f"round page did not contain selected home city label {home_city_label!r}")
    if loan_limit_text not in body_text:
        raise RuntimeError(f"round page did not contain expected loan limit text {loan_limit_text!r}")


def _submit_round(page: Page) -> None:
    page.locator("#open-submit-preview").click()
    page.locator("#decision-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    page.locator("#confirm-submit").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    page.locator("#confirm-submit").click()
    try:
        page.wait_for_url(
            re.compile(r".*/multi/rooms/[^/]+(?:/report)?$"),
            timeout=40_000,
            wait_until="domcontentloaded",
        )
    except Exception as exc:
        body_text = (page.locator("body").inner_text() or "").strip()
        raise RuntimeError(
            f"submit stayed on {page.url!r}; page starts with: {body_text[:1200]!r}"
        ) from exc


def _wait_for_round_report(page: Page, room_url: str, round_number: int) -> None:
    page.goto(f"{room_url}/report", wait_until="load")
    page.get_by_text("本轮财报", exact=True).wait_for()
    heading = (page.locator("h1").text_content() or "").upper()
    if f"R{round_number}" not in heading:
        raise RuntimeError(f"report heading missing expected round marker R{round_number}: {heading!r}")
    if page.locator(".report-grid-kpis .metric-card").count() < 3:
        raise RuntimeError("report KPI cards did not render")


def _assert_report_download(page: Page, output_path: Path) -> str:
    download_link = page.locator("#download-report-image").first
    download_link.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    tag_name = download_link.evaluate("(element) => element.tagName")
    if tag_name != "A":
        raise RuntimeError(f"report download control was not upgraded to anchor: {tag_name!r}")
    href = download_link.get_attribute("href") or ""
    if "download=1" not in href:
        raise RuntimeError(f"report download anchor missing download query: {href!r}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=30_000) as download_info:
        download_link.click()
    download = download_info.value
    if not download.suggested_filename.endswith(".png"):
        raise RuntimeError(f"report download filename is not a png: {download.suggested_filename!r}")
    download.save_as(str(output_path))
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"report download file was not saved correctly: {output_path}")
    return download.suggested_filename


def _advance_round(page: Page, room_url: str, round_number: int) -> None:
    page.goto(f"{room_url}/report", wait_until="load")
    page.get_by_role("button", name="进入下一轮决策").click()
    page.wait_for_url(f"{room_url}/game", wait_until="load")
    heading = (page.locator("h1").text_content() or "").upper()
    if f"R{round_number + 1}" not in heading:
        raise RuntimeError(f"next round form did not advance to R{round_number + 1}: {heading!r}")


def _wait_for_final(page: Page, room_url: str) -> None:
    page.goto(f"{room_url}/final", wait_until="load")
    body_text = (page.locator("body").inner_text() or "").strip()
    if "最终净资产" not in body_text:
        raise RuntimeError("final summary missing final net assets text")


def _capture(page: Page, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(output_path), full_page=True)


def _extract_metric_card_map(page: Page, selector: str) -> dict[str, str]:
    cards = page.locator(selector)
    result: dict[str, str] = {}
    for idx in range(cards.count()):
        card = cards.nth(idx)
        label = (card.locator("span").first.text_content() or "").strip()
        value = (card.locator("strong").first.text_content() or "").strip()
        if label:
            result[label] = value
    return result


def _parse_currency(text: str) -> float:
    cleaned = text.replace("¥", "").replace(",", "").strip()
    return float(cleaned or "0")


def _parse_rank(text: str) -> int | None:
    cleaned = text.replace("#", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


def _load_room_payload(room_id: str) -> dict[str, object]:
    raw = json.loads(MULTIPLAYER_ROOM_STORE_PATH.read_text(encoding="utf-8"))
    rooms = raw.get("rooms", {}) if isinstance(raw, dict) else {}
    if not isinstance(rooms, dict) or room_id not in rooms or not isinstance(rooms[room_id], dict):
        raise RuntimeError(f"room payload not found for {room_id}")
    return rooms[room_id]


def _room_member_by_client_id(room_payload: dict[str, object], client_id: str) -> dict[str, object]:
    members = room_payload.get("members", [])
    if not isinstance(members, list):
        raise RuntimeError("room members payload invalid")
    for item in members:
        if isinstance(item, dict) and str(item.get("client_id", "")) == client_id:
            return item
    raise RuntimeError(f"room member not found for client_id={client_id}")


def _report_consistency_summary(
    page: Page,
    room_id: str,
    host_client_id: str,
    *,
    expected_participants: int | None = None,
) -> dict[str, object]:
    room_payload = _load_room_payload(room_id)
    host_member = _room_member_by_client_id(room_payload, host_client_id)
    latest_report = host_member.get("latest_report_detail")
    if not isinstance(latest_report, dict):
        raise RuntimeError("host latest_report_detail missing after validation flow")

    report_metrics = _extract_metric_card_map(page, ".report-grid-kpis-three .metric-card")
    report_standings_rows = page.locator("section.report-section").nth(1).locator("tbody tr").count()
    body_text = (page.locator("body").inner_text() or "").lower()

    report_total_assets = _parse_currency(report_metrics.get("总资产", "0"))
    report_net_assets = _parse_currency(report_metrics.get("净资产", "0"))
    report_rank = _parse_rank(report_metrics.get("排名", ""))

    final_report = host_member.get("reports", [])
    final_reports_count = len(final_report) if isinstance(final_report, list) else 0

    return {
        "room_id": room_id,
        "host_team_id": str(host_member.get("team_id", "")),
        "report_round_id": str(latest_report.get("round_id", "")),
        "report_total_assets_matches_model": abs(report_total_assets - float(latest_report.get("total_assets", 0.0) or 0.0)) < 0.5,
        "report_net_assets_matches_model": abs(report_net_assets - float(latest_report.get("net_assets", 0.0) or 0.0)) < 0.5,
        "report_rank_matches_model": report_rank == latest_report.get("key_metrics", {}).get("预计排名"),
        "report_standings_row_count": report_standings_rows,
        "report_standings_matches_participants": (
            report_standings_rows == expected_participants if expected_participants is not None else None
        ),
        "report_has_no_nan_or_traceback": all(token not in body_text for token in ("traceback", "internal server error", "undefined", "nan")),
        "final_reports_count": final_reports_count,
        "final_net_assets": float(latest_report.get("net_assets", 0.0) or 0.0),
        "latest_report_total_assets": float(latest_report.get("total_assets", 0.0) or 0.0),
        "latest_report_net_assets": float(latest_report.get("net_assets", 0.0) or 0.0),
        "latest_report_rank": latest_report.get("key_metrics", {}).get("预计排名"),
    }


def main() -> int:
    args = _parse_args()
    if args.human_seats < 1:
        print("FAIL multiplayer browser flow: --human-seats must be >= 1", file=sys.stderr)
        return 1
    if args.bot_count < 0:
        print("FAIL multiplayer browser flow: --bot-count must be >= 0", file=sys.stderr)
        return 1
    if args.rounds < 1 or args.rounds > 4:
        print("FAIL multiplayer browser flow: --rounds must be between 1 and 4", file=sys.stderr)
        return 1

    base_url = args.base_url.rstrip("/") if args.base_url else ""
    port = 0
    if not base_url:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
    artifact_prefix = "domain" if args.base_url else "local"
    artifact_dir = args.screenshot_dir / f"{artifact_prefix}_humans_{args.human_seats}_bots_{args.bot_count}_rounds_{args.rounds}_{int(time.time())}"
    if args.base_url:
        if len(args.account_email) < args.human_seats:
            print(
                "FAIL multiplayer browser flow: --base-url mode requires one --account-email per human seat",
                file=sys.stderr,
            )
            return 1
        users = [_existing_user(email) for email in args.account_email[: args.human_seats]]
        server = None
    else:
        users = [_seed_user("multi-user", idx) for idx in range(args.human_seats)]
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "exschool_game.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=str(ROOT_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    try:
        if server is not None:
            _wait_for_server(base_url)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not args.headful)
            contexts = [browser.new_context(accept_downloads=True) for _ in range(args.human_seats)]
            pages = [context.new_page() for context in contexts]

            for page, (email, _name, _client_id) in zip(pages, users):
                _login(page, base_url, email, args.password)

            host_page = pages[0]
            room_url, room_code = _create_room(
                host_page,
                base_url,
                human_seats=args.human_seats,
                bot_count=args.bot_count,
                setup_screenshot_path=artifact_dir / "00-multiplayer-setup.png",
            )
            room_id = room_url.rstrip("/").rsplit("/", 1)[-1]
            _capture(host_page, artifact_dir / "01-room-created.png")

            for guest_page in pages[1:]:
                _join_room_by_code(guest_page, base_url, room_code)
            if len(pages) > 1 and args.guest_home_city_label:
                _set_home_city(pages[1], args.guest_home_city_label)
                _capture(pages[1], artifact_dir / "02-room-guest-home-city.png")

            expected_total = args.human_seats + args.bot_count
            for page in pages:
                page.goto(room_url, wait_until="load")
                _refresh_room(page)

            _assert_room_snapshot(
                host_page,
                expected_occupied=expected_total,
                expected_total=expected_total,
                expected_ready=args.bot_count,
                expected_bots=args.bot_count,
            )
            _capture(host_page, artifact_dir / "02-room-joined.png")

            for page in pages:
                _mark_ready(page)

            for page in pages:
                _refresh_room(page)

            _assert_room_snapshot(
                host_page,
                expected_occupied=expected_total,
                expected_total=expected_total,
                expected_ready=expected_total,
                expected_bots=args.bot_count,
            )
            host_page.locator("[data-testid='multiplayer-bots-panel'] summary").click()
            _capture(host_page, artifact_dir / "03-room-ready.png")
            _start_room(host_page, room_url)

            summary: dict[str, object] | None = None
            downloaded_report_filename: str | None = None
            for round_number in range(1, args.rounds + 1):
                for page in pages:
                    _open_current_round(page, room_url, round_number, args.human_seats)
                if round_number == 1 and len(pages) > 1 and args.guest_home_city_label:
                    _assert_round_contains_home_city(pages[1], args.guest_home_city_label, args.guest_home_city_loan_limit)
                if round_number == 1:
                    _capture(host_page, artifact_dir / "04-round-1-form.png")
                for page in pages:
                    _submit_round(page)
                for page in pages:
                    _wait_for_round_report(page, room_url, round_number)
                if round_number == 1:
                    _capture(host_page, artifact_dir / "05-round-1-report.png")
                    if args.check_download:
                        downloaded_report_filename = _assert_report_download(
                            host_page,
                            artifact_dir / "05-round-1-report-download.png",
                        )
                if round_number == args.rounds:
                    _capture(host_page, artifact_dir / f"06-round-{round_number}-report.png")
                    summary = _report_consistency_summary(
                        host_page,
                        room_id,
                        users[0][2],
                        expected_participants=args.human_seats + args.bot_count,
                    )

                if round_number < args.rounds:
                    _advance_round(host_page, room_url, round_number)
                    for page in pages[1:]:
                        _open_current_round(page, room_url, round_number + 1, args.human_seats)
                elif args.rounds == 4:
                    host_page.goto(f"{room_url}/report", wait_until="load")
                    host_page.get_by_role("button", name="查看最终总结").click()
                    host_page.wait_for_url(f"{room_url}/final", wait_until="load")
                    for page in pages:
                        _wait_for_final(page, room_url)
                    _capture(host_page, artifact_dir / "07-final-summary.png")

            if summary is None:
                raise RuntimeError("validation summary was not captured from the final report page")
            summary.update(
                {
                    "base_url": base_url,
                    "room_url": room_url,
                    "room_code": room_code,
                    "human_seats": args.human_seats,
                    "bot_count": args.bot_count,
                    "rounds": args.rounds,
                    "guest_home_city_label": args.guest_home_city_label if len(pages) > 1 else None,
                    "download_checked": args.check_download,
                    "downloaded_report_filename": downloaded_report_filename,
                    "screenshots": [str(path) for path in sorted(artifact_dir.glob("*.png"))],
                }
            )
            summary_path = artifact_dir / "summary.json"
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "message": f"multiplayer browser flow ({args.human_seats} humans + {args.bot_count} bots, {args.rounds} rounds)",
                        "summary_path": str(summary_path),
                        "artifact_dir": str(artifact_dir),
                        "room_id": room_id,
                    },
                    ensure_ascii=False,
                )
            )
            browser.close()
        return 0
    except PlaywrightTimeoutError as exc:
        print(f"FAIL multiplayer browser flow timeout: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FAIL multiplayer browser flow: {exc}", file=sys.stderr)
        return 1
    finally:
        if server is not None:
            with contextlib.suppress(Exception):
                server.terminate()
            with contextlib.suppress(Exception):
                server.wait(timeout=5)
            if server.poll() is None:
                with contextlib.suppress(Exception):
                    server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
