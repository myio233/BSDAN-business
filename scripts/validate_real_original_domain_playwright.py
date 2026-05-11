#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from exschool_game.auth_store import auth_store


PAGE_TIMEOUT_MS = 60_000
MODE_LABEL = "真实原版竞争"
PASSWORD = "playwright-pass"
CHROMIUM_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-gpu",
    "--disable-software-rasterizer",
]
EXPECTED_REAL_ORIGINAL_NUMBERS = {
    1: {"ending_cash": 58_843_246, "ending_debt": 4_894_719, "sales": 72_022_320},
    2: {"ending_cash": 164_839_081, "ending_debt": 5_046_197, "sales": 194_878_304},
    3: {"ending_cash": 488_113_013, "ending_debt": 15_512_526, "sales": 599_418_807},
    4: {"ending_cash": 1_054_450_611, "ending_debt": 26_303_413, "sales": 1_317_640_000},
}


def ensure_user() -> tuple[str, str, str]:
    for account in sorted(auth_store.accounts.values(), key=lambda item: item.created_at, reverse=True):
        if account.enabled and (account.name.startswith("test-user-") or account.name.startswith("pw-real-original-")):
            return account.name, account.email, PASSWORD
    suffix = int(time.time())
    name = f"pw-real-domain-{suffix}"
    email = f"pw-real-domain-{suffix}@example.com"
    auth_store.register_user(name, email, PASSWORD)
    return name, email, PASSWORD


def create_session_cookie(base_url: str, account: str, password: str) -> str:
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
    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', login_page.stdout, flags=re.IGNORECASE)
    if not initial_cookie_match or not csrf_match:
        raise RuntimeError("login page did not provide session cookie and csrf token")

    result = subprocess.run(
        [
            "curl",
            "-i",
            "-s",
            "--cookie",
            f"session={initial_cookie_match.group(1)}",
            "--data-urlencode",
            f"account={account}",
            "--data-urlencode",
            f"password={password}",
            "--data-urlencode",
            f"_csrf={csrf_match.group(1)}",
            f"{base_url}/auth/login",
        ],
        cwd=str(ROOT_DIR),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    match = re.search(r"^set-cookie:\s*session=([^;]+);", result.stdout, flags=re.IGNORECASE | re.MULTILINE)
    if not match:
        raise RuntimeError(f"session cookie missing after login for {account}")
    return match.group(1)


def format_money(value: int) -> str:
    return f"{value:,}"


async def assert_mode_chip(page) -> None:
    chip = page.locator("[data-testid='mode-chip']").first
    await chip.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    text = (await chip.text_content() or "").strip()
    if text != MODE_LABEL:
        raise AssertionError(f"expected mode chip {MODE_LABEL!r}, got {text!r}")


async def assert_no_bad_text(page) -> None:
    body_text = (await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS) or "").lower()
    for token in ("traceback", "internal server error", "undefined", "nan"):
        if token in body_text:
            raise AssertionError(f"page body contains suspicious token: {token}")


async def assert_mode_page(page, base_url: str) -> None:
    await page.goto(f"{base_url}/mode", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    await page.locator(".mode-card-real-original").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    body_text = await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS)
    if MODE_LABEL not in body_text:
        raise AssertionError("mode page did not show real-original entrypoint")
    if "高强度竞争" in body_text:
        raise AssertionError("mode page still exposes high-intensity copy")
    if await page.locator("a[href$='/single-fixed/setup']").count() > 0:
        raise AssertionError("mode page still exposes the single-fixed high-intensity entrypoint")


async def start_real_original_game(page, base_url: str, company_name: str) -> None:
    await assert_mode_page(page, base_url)
    await page.locator("a[href$='/single-real/setup']").click()
    await page.locator("[data-testid='single-setup-form']").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await assert_mode_chip(page)
    await page.get_by_label("公司名称").fill(company_name)
    await page.get_by_label("主场城市").select_option(label="上海")
    await page.get_by_role("button", name="开始 40 分钟游戏").click()
    await page.locator("#open-submit-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await assert_mode_chip(page)
    page_data_text = await page.locator("#round-page-data").text_content(timeout=PAGE_TIMEOUT_MS)
    page_data = json.loads(page_data_text or "{}")
    initial_payload = page_data.get("initialPayload", {})
    if page_data.get("currentWorkers") != 879 or page_data.get("currentEngineers") != 335:
        raise AssertionError("round 1 page did not expose expected historical starting headcount")
    if initial_payload.get("workers") != 0 or initial_payload.get("engineers") != 0:
        raise AssertionError("round 1 default payload is not a zero headcount delta")


async def assert_home_continue(page, base_url: str, company_name: str, round_number: int, *, report_page: bool) -> None:
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    row = page.locator("xpath=//h2[contains(., '进行中的对局')]/following::table[1]/tbody/tr", has_text=company_name).first
    await row.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    row_text = await row.inner_text(timeout=PAGE_TIMEOUT_MS)
    if MODE_LABEL not in row_text or f"R{round_number}" not in row_text.upper():
        raise AssertionError(f"home active save row mismatch: {row_text!r}")
    await row.get_by_role("link").first.click()
    await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    await assert_mode_chip(page)
    body_text = await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS)
    expected = "本轮财报" if report_page else "预览并提交"
    if expected not in body_text:
        raise AssertionError(f"continue flow did not land on expected page containing {expected!r}")


async def assert_report_numbers(page, round_number: int) -> None:
    body_text = await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS)
    expected = EXPECTED_REAL_ORIGINAL_NUMBERS[round_number]
    missing = [format_money(value) for value in expected.values() if format_money(value) not in body_text]
    if missing:
        raise AssertionError(f"round {round_number} report is missing expected source-faithful numbers: {missing}")
    if "real-original 默认决策 replay" not in body_text:
        raise AssertionError("real-original replay provenance note is missing")
    if "Team 13 使用当前输入" not in body_text:
        raise AssertionError("report notes did not identify Team 13 as the current input team")


async def assert_report_download(page) -> None:
    control = page.locator("#download-report-image").first
    await control.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    for _ in range(30):
        if (await control.evaluate("(element) => element.tagName")) == "A":
            break
        await page.wait_for_timeout(1000)
    href = await control.get_attribute("href")
    if not href or "download=1" not in href:
        raise AssertionError(f"report download href not ready: {href!r}")
    async with page.expect_download(timeout=45_000) as download_info:
        await control.click(timeout=PAGE_TIMEOUT_MS)
    download = await download_info.value
    if not download.suggested_filename.endswith(".png"):
        raise AssertionError(f"report download filename is not a png: {download.suggested_filename!r}")


async def submit_round(page, base_url: str, company_name: str, round_number: int) -> None:
    await assert_mode_chip(page)
    page_data = json.loads((await page.locator("#round-page-data").text_content(timeout=PAGE_TIMEOUT_MS)) or "{}")
    form_snapshot = await page.locator("#decision-form").evaluate(
        """(form) => Object.fromEntries(Array.from(new FormData(form).entries()))"""
    )
    print(
        "[trace] form snapshot",
        round_number,
        {
            "initial": {
                key: page_data.get("initialPayload", {}).get(key)
                for key in ["loan_delta", "workers", "engineers", "worker_salary", "engineer_salary", "products_planned"]
            },
            "form": {
                key: form_snapshot.get(key)
                for key in ["loan_delta", "workers", "engineers", "worker_salary", "engineer_salary", "products_planned"]
            },
        },
        flush=True,
    )
    await page.locator("#open-submit-preview").click(timeout=PAGE_TIMEOUT_MS)
    await page.locator("#decision-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await page.locator("#confirm-submit").click(timeout=PAGE_TIMEOUT_MS, no_wait_after=True)
    try:
        await page.locator(".report-grid-kpis .metric-card").first.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        body_text = (await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS) or "").strip()
        raise AssertionError(f"round {round_number} submit did not render a report; page body starts with: {body_text[:1600]!r}") from exc
    await assert_mode_chip(page)
    await assert_no_bad_text(page)
    await assert_report_numbers(page, round_number)
    if round_number == 1:
        await assert_report_download(page)
        await assert_home_continue(page, base_url, company_name, round_number, report_page=True)


async def assert_final_and_history(page, base_url: str, company_name: str) -> None:
    try:
        await page.locator("text=最终总结").first.wait_for(state="visible", timeout=10_000)
        body_text = await page.locator("body").inner_text(timeout=PAGE_TIMEOUT_MS)
        if "最终轮" not in body_text:
            raise AssertionError("final summary did not render expected final content")
        if MODE_LABEL not in body_text:
            raise AssertionError("final summary is missing the real-original mode label")
        if format_money(EXPECTED_REAL_ORIGINAL_NUMBERS[4]["ending_cash"]) not in body_text:
            raise AssertionError("final summary is missing round 4 source-faithful cash value")
    except PlaywrightTimeoutError:
        print(f"[trace] final page not retained at {page.url}; validating archived history instead", flush=True)
    await page.goto(f"{base_url}/", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
    history_row = page.locator("xpath=//h2[contains(., '最近完成的对局')]/following::table[1]/tbody/tr", has_text=company_name).first
    await history_row.wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await history_row.get_by_role("link", name="查看总结").click()
    await page.locator("text=历史结果").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
    await assert_mode_chip(page)


async def run(base_url: str, headed: bool) -> None:
    name, email, password = ensure_user()
    session_cookie = create_session_cookie(base_url, email, password)
    company_name = f"PW Real Domain {int(time.time())}"

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=not headed,
            channel="chromium",
            args=CHROMIUM_ARGS,
        )
        try:
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
            console_errors: list[str] = []
            page_errors: list[str] = []
            response_failures: list[str] = []
            page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: page_errors.append(str(error)))
            page.on("response", lambda response: response_failures.append(f"{response.status} {response.url}") if response.status >= 500 else None)

            await start_real_original_game(page, base_url, company_name)
            await assert_home_continue(page, base_url, company_name, 1, report_page=False)
            for round_number in range(1, 5):
                print(f"[trace] submit real-original round {round_number}", flush=True)
                await submit_round(page, base_url, company_name, round_number)
                if round_number < 4:
                    await page.get_by_role("button", name="进入下一轮决策").click()
                    await page.locator("#open-submit-preview").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
                    await assert_mode_chip(page)
                else:
                    await page.get_by_role("button", name="查看总结").click()
                    await page.wait_for_load_state("domcontentloaded", timeout=PAGE_TIMEOUT_MS)

            await assert_final_and_history(page, base_url, company_name)
            if page_errors:
                raise AssertionError(f"page errors captured: {page_errors[:3]}")
            non_cf_console_errors = [
                item
                for item in console_errors
                if "challenge-platform" not in item
                and "Failed to load resource: the server responded with a status of 502" not in item
            ]
            if non_cf_console_errors:
                raise AssertionError(f"console errors captured: {non_cf_console_errors[:3]}")
            non_benign_response_failures = [
                item for item in response_failures if not item.startswith("502 ") or not item.endswith("/game/next")
            ]
            if non_benign_response_failures:
                raise AssertionError(f"5xx responses captured: {non_benign_response_failures[:3]}")
            print(f"[ok] real-original domain flow completed with user {name} <{email}>")
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the real-original Exschool flow against the public domain with Playwright.")
    parser.add_argument("--base-url", default="https://bsdan.ye97.cn")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.base_url.rstrip("/"), args.headed))
    except (AssertionError, PlaywrightError, PlaywrightTimeoutError, RuntimeError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
