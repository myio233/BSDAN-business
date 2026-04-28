#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from contextlib import suppress
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from exschool_game.auth_store import auth_store


MODES = {
    "high-intensity": {
        "label": "高强度竞争",
        "selector": "a[href$='/single-fixed/setup']",
        "setup_path": "/single-fixed/setup",
        "start_path": "/single-fixed/start",
    },
    "real-original": {
        "label": "真实原版竞争",
        "selector": "a[href$='/single-real/setup']",
        "setup_path": "/single-real/setup",
        "start_path": "/single-real/start",
    },
}
PAGE_TIMEOUT_MS = 60_000
CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
]
REPORT_IMAGE_ROUTE_PREFIX = "/game/report-image/"


def is_report_image_request_url(url: str) -> bool:
    try:
        return urlparse(url).path.startswith(REPORT_IMAGE_ROUTE_PREFIX)
    except Exception:
        return False


def is_benign_report_image_console_error(item: dict[str, object]) -> bool:
    text = str(item.get("text", "") or "")
    location = item.get("location")
    location_url = ""
    if isinstance(location, dict):
        location_url = str(location.get("url", "") or "")
    return (
        "Failed to load resource: the server responded with a status of 404 (Not Found)" in text
        and is_report_image_request_url(location_url)
    )


def is_benign_report_image_request_failure(item: dict[str, object]) -> bool:
    return str(item.get("failure", "") or "") == "net::ERR_ABORTED" and is_report_image_request_url(
        str(item.get("url", "") or "")
    )


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urlopen(base_url, timeout=2) as response:
                if response.status < 500:
                    return
        except URLError:
            time.sleep(0.2)
    raise RuntimeError(f"server did not start within {timeout_seconds:.1f}s: {base_url}")


def ensure_user(mode: str) -> tuple[str, str, str]:
    suffix = f"{int(time.time())}-{mode}"
    name = f"pw-{mode}-{suffix}"
    email = f"pw-{mode}-{suffix}@example.com"
    password = "playwright-pass"
    auth_store.register_user(name, email, password)
    return name, email, password


def start_server(port: int) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.setdefault("EXSCHOOL_HOST", "127.0.0.1")
    env["EXSCHOOL_PORT"] = str(port)
    env.setdefault("EXSCHOOL_SESSION_SECRET", "playwright-secret")
    env.setdefault("EXSCHOOL_DISABLE_REPORT_IMAGE_PREWARM", "1")
    return subprocess.Popen(
        [str(ROOT_DIR / ".venv" / "bin" / "python"), "-m", "uvicorn", "exschool_game.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop_server(server: subprocess.Popen[str] | None) -> None:
    if server is None:
        return
    with suppress(ProcessLookupError):
        server.terminate()
    with suppress(subprocess.TimeoutExpired):
        server.wait(timeout=5)
    if server.poll() is None:
        with suppress(ProcessLookupError):
            server.kill()


def create_session_cookie(base_url: str, account: str, password: str) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            login_page = subprocess.run(
                ["curl", "-i", "-s", f"{base_url}/auth?mode=login"],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            initial_cookie_match = re.search(
                r"^set-cookie:\s*session=([^;]+);",
                login_page.stdout,
                flags=re.IGNORECASE | re.MULTILINE,
            )
            csrf_match = re.search(
                r'<meta name="csrf-token" content="([^"]+)"',
                login_page.stdout,
                flags=re.IGNORECASE,
            )
            if not initial_cookie_match or not csrf_match:
                raise RuntimeError("login page did not provide session cookie and csrf token")
            initial_session_cookie = initial_cookie_match.group(1)
            csrf_token = csrf_match.group(1)
            result = subprocess.run(
                [
                    "curl",
                    "-i",
                    "-s",
                    "--cookie",
                    f"session={initial_session_cookie}",
                    "--data-urlencode",
                    f"account={account}",
                    "--data-urlencode",
                    f"password={password}",
                    "--data-urlencode",
                    f"_csrf={csrf_token}",
                    f"{base_url}/auth/login",
                ],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
            match = re.search(r"^set-cookie:\s*session=([^;]+);", result.stdout, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(1)
            last_error = RuntimeError(f"session cookie missing after login for {account}")
        except Exception as exc:  # pragma: no cover - exercised in flaky server startup paths
            last_error = exc
        time.sleep(0.5 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"session cookie missing after login for {account}")


async def ensure_browser_login(page, base_url: str, account: str, password: str) -> None:
    session_cookie = create_session_cookie(base_url, account, password)
    await page.context.add_cookies(
        [
            {
                "name": "session",
                "value": session_cookie,
                "url": base_url,
                "httpOnly": True,
                "sameSite": "Lax",
            }
        ]
    )
    for target_path in ("/mode", ""):
        await page.goto(f"{base_url}{target_path}", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        if "/auth" not in page.url:
            return
    raise AssertionError(f"browser login fallback did not leave auth page for account {account!r}: {page.url!r}")


async def assert_mode_chip(page, expected_label: str) -> None:
    chip = page.locator("[data-testid='mode-chip']").first
    text = (await chip.text_content() or "").strip()
    if text != expected_label:
        raise AssertionError(f"expected mode label {expected_label!r}, got {text!r}")


async def assert_no_page_errors(
    console_errors: list[dict[str, object]],
    page_errors: list[str],
    request_failures: list[dict[str, object]],
    response_failures: list[dict[str, object]],
) -> None:
    non_benign_console_errors = [item for item in console_errors if not is_benign_report_image_console_error(item)]
    non_benign_request_failures = [item for item in request_failures if not is_benign_report_image_request_failure(item)]
    if page_errors:
        raise AssertionError(f"page errors captured: {page_errors[:3]}")
    if non_benign_console_errors:
        raise AssertionError(
            "console errors captured: "
            f"{[{key: item.get(key) for key in ('text', 'location')} for item in non_benign_console_errors[:3]]}; "
            f"request failures: {non_benign_request_failures[:3]}"
        )
    if non_benign_request_failures:
        raise AssertionError(f"request failures captured: {non_benign_request_failures[:3]}")
    if response_failures:
        raise AssertionError(f"http responses with 4xx/5xx captured: {response_failures[:3]}")


async def assert_round_report_sanity(page) -> None:
    standings_section_xpath = (
        "//section[.//h2[contains(., '本轮全公司') and contains(., '排名')]]"
        "|//section[.//h3[contains(., '本轮全公司') and contains(., '排名')]]"
    )
    standings_rows = page.locator(f"xpath=({standings_section_xpath})[1]//table[1]/tbody/tr")
    standings_count = await standings_rows.count()
    if standings_count < 20:
        raise AssertionError(f"expected at least 20 company rows in round standings, got {standings_count}")
    standings_text = (await page.locator(f"xpath=({standings_section_xpath})[1]//table[1]").text_content() or "").strip()
    if "13" not in standings_text:
        raise AssertionError("round standings table does not include team 13")
    body_text = (await page.locator("body").inner_text() or "").lower()
    for token in ("traceback", "internal server error", "undefined", "nan"):
        if token in body_text:
            raise AssertionError(f"page body contains suspicious token: {token}")


async def assert_round_form_sanity(page, expected_label: str, round_number: int) -> None:
    await assert_mode_chip(page, expected_label)
    await page.locator("h1").first.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    heading = (await page.locator("h1").first.text_content() or "").upper()
    if f"R{round_number}" not in heading:
        raise AssertionError(f"round form heading missing round marker for round {round_number}: {heading!r}")
    if await page.locator("#open-submit-preview").count() < 1:
        raise AssertionError("round form missing preview submit button")


async def assert_final_report_sanity(page) -> None:
    final_section_xpath = (
        "//section[.//h2[contains(., '最终轮') and contains(., '排名')]]"
        "|//section[.//h3[contains(., '最终轮') and contains(., '排名')]]"
    )
    await page.locator(f"xpath=({final_section_xpath})[1]").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    final_rows = page.locator(f"xpath=({final_section_xpath})[1]//table[1]/tbody/tr")
    final_count = await final_rows.count()
    if final_count < 20:
        raise AssertionError(f"expected at least 20 company rows in final standings, got {final_count}")
    final_text = (await page.locator(f"xpath=({final_section_xpath})[1]//table[1]").text_content() or "").strip()
    if "13" not in final_text:
        raise AssertionError("final standings table does not include team 13")


async def submit_round_and_validate(page, base_url: str, expected_label: str, round_number: int) -> None:
    await assert_mode_chip(page, expected_label)
    await page.locator("#open-submit-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await page.locator("#open-submit-preview").click(timeout=PAGE_TIMEOUT_MS)
    await page.locator("#decision-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await page.locator("#confirm-submit").click(timeout=PAGE_TIMEOUT_MS, no_wait_after=True)
    await page.locator(".report-grid-kpis .metric-card").first.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await assert_mode_chip(page, expected_label)

    heading = await page.locator("h1").first.text_content()
    if f"R{round_number}" not in (heading or "").upper():
        raise AssertionError(f"report heading missing round marker for round {round_number}: {heading!r}")

    metrics = page.locator(".report-grid-kpis .metric-card")
    if await metrics.count() < 3:
        raise AssertionError("report KPI cards did not render")

    await assert_round_report_sanity(page)


async def find_active_save_row(page, expected_company_name: str, expected_label: str, expected_round: int):
    active_rows = page.locator("xpath=//h2[contains(., '进行中的对局')]/following::table[1]/tbody/tr")
    matching_rows = active_rows.filter(has_text=expected_company_name).filter(has_text=expected_label)
    match_count = await matching_rows.count()
    if match_count != 1:
        row_texts: list[str] = []
        for index in range(await active_rows.count()):
            row_texts.append(((await active_rows.nth(index).inner_text()) or "").strip())
        raise AssertionError(
            f"expected exactly one active save row for company {expected_company_name!r} and mode {expected_label!r}, "
            f"got {match_count}; active rows: {row_texts}"
        )

    row = matching_rows.first
    cells = row.locator("td")
    company_text = ((await cells.nth(0).text_content()) or "").strip()
    mode_text = ((await cells.nth(1).text_content()) or "").strip()
    round_text = ((await cells.nth(3).text_content()) or "").strip()
    normalized_round_text = round_text.upper().removeprefix("R")
    if company_text != expected_company_name:
        raise AssertionError(f"expected active save company {expected_company_name!r}, got {company_text!r}")
        if not mode_text.startswith(expected_label):
            raise AssertionError(f"expected active save mode {expected_label!r}, got {mode_text!r}")
    if normalized_round_text != str(expected_round):
        raise AssertionError(f"expected active save round {expected_round}, got {round_text!r}")
    return row


async def assert_home_continue(
    page,
    base_url: str,
    expected_company_name: str,
    expected_label: str,
    expected_round: int,
    *,
    report_page: bool,
) -> None:
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    row = await find_active_save_row(page, expected_company_name, expected_label, expected_round)
    continue_link = row.get_by_role("link").first
    continue_href = await continue_link.get_attribute("href")
    if not continue_href or "game_id=" not in continue_href:
        raise AssertionError(
            f"home page continue link did not include game_id for company {expected_company_name!r} round {expected_round}: {continue_href!r}"
        )
    continue_url = continue_href if continue_href.startswith("http") else f"{base_url}{continue_href}"
    await page.goto(continue_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    await assert_mode_chip(page, expected_label)
    body_text = await page.locator("body").inner_text()
    if report_page:
        if "本轮财报" not in (body_text or ""):
            raise AssertionError("continue flow should land on submitted report page, but report content was missing")
    else:
        if "预览并提交" not in (body_text or ""):
            raise AssertionError("continue flow should land on editable round form, but preview submit button was missing")


async def assert_restart_returns_mode_select(page, base_url: str) -> None:
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    mode_links = page.locator(f"a[href='{base_url}/mode']")
    if await mode_links.count() < 1:
        raise AssertionError("home page did not expose a restart/new-game link to /mode")
    await mode_links.last.click()
    await page.wait_for_url(f"{base_url}/mode", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    page_text = await page.locator("body").inner_text()
    if "选择这一局的游戏模式" not in (page_text or ""):
        raise AssertionError("restart flow did not return to mode selection page")


async def assert_report_download(page) -> None:
    download_link = page.locator("#download-report-image").first
    await download_link.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    if (await download_link.evaluate("(element) => element.tagName")) != "A":
        raise AssertionError("report download control is not a direct anchor element")
    href = await download_link.get_attribute("href")
    if not href or "download=1" not in href:
        raise AssertionError(f"report download anchor missing direct download href: {href!r}")
    async with page.expect_download(timeout=30000) as download_info:
        await download_link.click(timeout=PAGE_TIMEOUT_MS)
    download = await download_info.value
    if not download.suggested_filename.endswith(".png"):
        raise AssertionError("report download did not produce a png file")


async def start_mode_game(
    page,
    base_url: str,
    mode: str,
    company_name: str,
    account: str,
    password: str,
    *,
    include_home_continue_check: bool = True,
) -> None:
    config = MODES[mode]
    mode_card = page.locator(config["selector"])
    last_failure_context: tuple[str, str] | None = None
    for attempt in range(3):
        await page.goto(f"{base_url}/mode", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        try:
            await mode_card.wait_for(state="visible", timeout=10_000)
            break
        except PlaywrightTimeoutError:
            body_text = ((await page.locator("body").inner_text()) or "").strip()
            last_failure_context = (page.url, body_text[:1200])
            if "/auth" in page.url:
                await ensure_browser_login(page, base_url, account, password)
                continue
            if attempt == 2:
                break
            await page.wait_for_timeout(500 * (attempt + 1))
    else:  # pragma: no cover - loop exits via break paths above
        last_failure_context = None
    if last_failure_context is not None and not await mode_card.is_visible():
        current_url, body_text = last_failure_context
        raise AssertionError(
            f"mode card did not become visible for mode {mode} at {current_url!r}; "
            f"page body starts with: {body_text!r}"
        )
    await page.locator(config["selector"]).click()
    try:
        await page.locator("[data-testid='single-setup-form']").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        body_text = (await page.locator("body").inner_text() or "").strip()
        raise AssertionError(
            f"setup form did not become visible for mode {mode} at {page.url!r}; page body starts with: {body_text[:1200]!r}"
        ) from exc
    await assert_mode_chip(page, config["label"])

    await page.get_by_label("公司名称").fill(company_name)
    await page.get_by_label("主场城市").select_option(index=0)
    await page.get_by_role("button", name="开始 40 分钟游戏").click()
    await page.locator("#open-submit-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await assert_round_form_sanity(page, config["label"], 1)
    if include_home_continue_check:
        await assert_home_continue(page, base_url, company_name, config["label"], 1, report_page=False)


async def run_mode(
    page,
    base_url: str,
    mode: str,
    account: str,
    password: str,
    *,
    include_extra_flow_checks: bool = True,
) -> None:
    config = MODES[mode]
    company_name = f"PW {mode.title()} Co"

    print(f"[trace] start mode {mode}", flush=True)
    await start_mode_game(
        page,
        base_url,
        mode,
        company_name,
        account,
        password,
        include_home_continue_check=include_extra_flow_checks,
    )

    for round_number in range(1, 5):
        print(f"[trace] {mode} round {round_number}", flush=True)
        await submit_round_and_validate(page, base_url, config["label"], round_number)
        if round_number == 1 and include_extra_flow_checks:
            await assert_home_continue(page, base_url, company_name, config["label"], round_number, report_page=True)
            await assert_restart_returns_mode_select(page, base_url)
            await page.goto(f"{base_url}/game", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            await assert_mode_chip(page, config["label"])
            await assert_report_download(page)
        if round_number < 4:
            await page.get_by_role("button", name="进入下一轮决策").click()
            await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            await assert_round_form_sanity(page, config["label"], round_number + 1)
        else:
            await page.get_by_role("button", name="查看总结").click()
            await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)

    await assert_mode_chip(page, config["label"])
    await assert_final_report_sanity(page)

    body_text = await page.locator("body").inner_text()
    if "最终净资产" not in (body_text or ""):
        raise AssertionError("final summary missing final net assets label")


async def run_multi_save_history(page, base_url: str, account: str, password: str) -> None:
    alpha_company = "PW Save Alpha"
    beta_company = "PW Save Beta"

    print("[trace] multi-save alpha start", flush=True)
    await start_mode_game(page, base_url, "high-intensity", alpha_company, account, password)
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    home_text = await page.locator("body").inner_text()
    if "进行中的对局" not in (home_text or "") or alpha_company not in (home_text or ""):
        raise AssertionError("home page did not show the first active save")

    print("[trace] multi-save beta start", flush=True)
    await start_mode_game(page, base_url, "real-original", beta_company, account, password)
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    home_text = await page.locator("body").inner_text()
    if alpha_company not in (home_text or "") or beta_company not in (home_text or ""):
        raise AssertionError("home page did not list both active saves")

    print("[trace] multi-save select alpha", flush=True)
    alpha_row = await find_active_save_row(page, alpha_company, MODES["high-intensity"]["label"], 1)
    await alpha_row.get_by_role("link").first.click()
    await page.locator("#open-submit-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await assert_mode_chip(page, MODES["high-intensity"]["label"])
    if alpha_company not in ((await page.locator("body").inner_text()) or ""):
        raise AssertionError("selecting the first active save did not reopen the correct company")

    for round_number in range(1, 5):
        print(f"[trace] multi-save alpha round {round_number}", flush=True)
        await submit_round_and_validate(page, base_url, MODES["high-intensity"]["label"], round_number)
        if round_number < 4:
            await page.get_by_role("button", name="进入下一轮决策").click()
            await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
            await assert_round_form_sanity(page, MODES["high-intensity"]["label"], round_number + 1)
        else:
            await page.get_by_role("button", name="查看总结").click()
            await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)

    print("[trace] multi-save home after alpha final", flush=True)
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    home_text = await page.locator("body").inner_text()
    if beta_company not in (home_text or ""):
        raise AssertionError("completing one save removed the other active save")
    if alpha_company not in (home_text or "") or "最近完成的对局" not in (home_text or ""):
        raise AssertionError("completed save did not appear in history")

    print("[trace] multi-save open history", flush=True)
    history_row = page.locator("tr", has_text=alpha_company).last
    await history_row.get_by_role("link", name="查看总结").click(no_wait_after=True)
    await page.locator("text=历史结果").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    if "/history/" not in page.url:
        raise AssertionError(f"history detail did not navigate to a history URL: {page.url!r}")
    page_text = await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS)
    if "历史结果" not in (page_text or ""):
        raise AssertionError("history detail did not render as a read-only final summary")
    await assert_mode_chip(page, MODES["high-intensity"]["label"])


async def run(
    base_url: str,
    headed: bool,
    credentials: dict[str, tuple[str, str, str]],
    scenarios: list[str] | None = None,
    browser_name: str = "chromium",
) -> None:
    requested_scenarios = scenarios or [*MODES.keys(), "multi-save"]
    async with async_playwright() as playwright:
        async def launch_browser():
            browser_type = getattr(playwright, browser_name)
            launch_kwargs = {"headless": not headed}
            if browser_name == "chromium":
                launch_kwargs["args"] = CHROMIUM_ARGS
                launch_kwargs["channel"] = "chromium"
            return await browser_type.launch(**launch_kwargs)

        for mode in [item for item in requested_scenarios if item in MODES]:
            browser = await launch_browser()
            try:
                name, email, password = credentials[mode]
                session_cookie = create_session_cookie(base_url, email, password)
                context = await browser.new_context()
                await context.add_cookies(
                    [
                        {
                            "name": "session",
                            "value": session_cookie,
                            "url": base_url,
                            "httpOnly": True,
                            "sameSite": "Lax",
                        }
                    ]
                )
                page = await context.new_page()
                console_errors: list[dict[str, object]] = []
                page_errors: list[str] = []
                request_failures: list[dict[str, object]] = []
                response_failures: list[dict[str, object]] = []
                page.on(
                    "console",
                    lambda message: console_errors.append(
                        {
                            "type": message.type,
                            "text": message.text,
                            "location": message.location,
                        }
                    )
                    if message.type == "error"
                    else None,
                )
                page.on("pageerror", lambda error: page_errors.append(str(error)))
                page.on(
                    "requestfailed",
                    lambda request: request_failures.append(
                        {
                            "url": request.url,
                            "method": request.method,
                            "resource_type": request.resource_type,
                            "failure": request.failure,
                        }
                    ),
                )
                page.on(
                    "response",
                    lambda response: response_failures.append(
                        {
                            "url": response.url,
                            "status": response.status,
                            "request": {
                                "method": response.request.method,
                                "resource_type": response.request.resource_type,
                            },
                        }
                    )
                    if response.status >= 400
                    else None,
                )
                try:
                    await run_mode(page, base_url, mode, email, password, include_extra_flow_checks=(mode == "high-intensity"))
                    await assert_no_page_errors(console_errors, page_errors, request_failures, response_failures)
                    print(f"[ok] {mode} completed with user {name}")
                finally:
                    await context.close()
            finally:
                await browser.close()

        if "multi-save" not in requested_scenarios:
            return

        browser = await launch_browser()
        try:
            name, email, password = credentials["multi-save"]
            session_cookie = create_session_cookie(base_url, email, password)
            context = await browser.new_context()
            await context.add_cookies(
                [
                    {
                        "name": "session",
                        "value": session_cookie,
                        "url": base_url,
                        "httpOnly": True,
                        "sameSite": "Lax",
                    }
                ]
            )
            page = await context.new_page()
            console_errors = []
            page_errors = []
            request_failures = []
            response_failures = []
            page.on(
                "console",
                lambda message: console_errors.append(
                    {
                        "type": message.type,
                        "text": message.text,
                        "location": message.location,
                    }
                )
                if message.type == "error"
                else None,
            )
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on(
                "requestfailed",
                lambda request: request_failures.append(
                    {
                        "url": request.url,
                        "method": request.method,
                        "resource_type": request.resource_type,
                        "failure": request.failure,
                    }
                ),
            )
            page.on(
                "response",
                lambda response: response_failures.append(
                    {
                        "url": response.url,
                        "status": response.status,
                        "request": {
                            "method": response.request.method,
                            "resource_type": response.request.resource_type,
                        },
                    }
                )
                if response.status >= 400
                else None,
            )
            try:
                await run_multi_save_history(page, base_url, email, password)
                await assert_no_page_errors(console_errors, page_errors, request_failures, response_failures)
                print(f"[ok] multi-save history completed with user {name}")
            finally:
                await context.close()
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate both Exschool single-player modes through all four rounds with Playwright.")
    parser.add_argument("--port", type=int, default=0, help="Port for the temporary local server. Defaults to a free port.")
    parser.add_argument("--headed", action="store_true", help="Run Playwright in headed mode.")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[*MODES.keys(), "multi-save"],
        help="Run only the selected scenario. Can be passed multiple times.",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "firefox"],
        default="chromium",
        help="Playwright browser to use for validation.",
    )
    args = parser.parse_args()

    port = args.port or find_free_port()
    base_url = f"http://127.0.0.1:{port}"
    requested_scenarios = args.scenario or [*MODES.keys(), "multi-save"]
    credentials = {mode: ensure_user(mode) for mode in MODES if mode in requested_scenarios}
    if "multi-save" in requested_scenarios:
        credentials["multi-save"] = ensure_user("multi-save")
    server: subprocess.Popen[str] | None = None
    try:
        last_start_error: RuntimeError | None = None
        for _ in range(3):
            server = start_server(port)
            try:
                wait_for_server(base_url)
                break
            except RuntimeError as exc:
                last_start_error = exc
                stop_server(server)
                server = None
                time.sleep(1.0)
        else:
            assert last_start_error is not None
            raise last_start_error
        asyncio.run(run(base_url, args.headed, credentials, requested_scenarios, args.browser))
    except (AssertionError, PlaywrightError, PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        stop_server(server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
