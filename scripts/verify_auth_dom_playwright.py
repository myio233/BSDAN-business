#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncore
import asyncio
import os
import re
import socket
import smtpd
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from queue import Queue
from textwrap import dedent

from playwright.async_api import async_playwright


ROOT_DIR = Path(__file__).resolve().parents[1]
CHROMIUM_PATH = os.environ.get("CHROMIUM_PATH", "/snap/bin/chromium")
PAGE_TIMEOUT_MS = 30_000


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_server(base_url: str, timeout_seconds: float = 60.0) -> None:
    deadline = time.time() + timeout_seconds
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            import urllib.request

            with urllib.request.urlopen(base_url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as exc:  # pragma: no cover - startup race
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"server did not start within {timeout_seconds:.1f}s: {base_url}") from last_error


class CaptureSMTPServer(smtpd.SMTPServer):
    def __init__(self, localaddr: tuple[str, int]) -> None:
        super().__init__(localaddr, None)
        self.messages: Queue[dict[str, object]] = Queue()

    def process_message(self, peer, mailfrom, rcpttos, data, **kwargs):  # type: ignore[override]
        self.messages.put(
            {
                "peer": peer,
                "mailfrom": mailfrom,
                "rcpttos": rcpttos,
                "data": data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data),
            }
        )


def start_smtp_server() -> tuple[CaptureSMTPServer, threading.Event, threading.Thread, int]:
    port = find_free_port()
    server = CaptureSMTPServer(("127.0.0.1", port))
    stop_event = threading.Event()

    def loop() -> None:
        while not stop_event.is_set():
            asyncore.loop(timeout=0.1, count=1)

    thread = threading.Thread(target=loop, name="smtp-capture", daemon=True)
    thread.start()
    return server, stop_event, thread, port


def stop_smtp_server(server: CaptureSMTPServer, stop_event: threading.Event, thread: threading.Thread) -> None:
    stop_event.set()
    server.close()
    thread.join(timeout=5)


def start_app_server(port: int, smtp_port: int, sitecustomize_dir: Path) -> tuple[subprocess.Popen[str], list[str]]:
    env = os.environ.copy()
    env.update(
        {
            "EXSCHOOL_HOST": "127.0.0.1",
            "EXSCHOOL_PORT": str(port),
            "EXSCHOOL_SESSION_SECRET": "verify-auth-dom-secret",
            "EXSCHOOL_DISABLE_REPORT_IMAGE_PREWARM": "1",
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": str(smtp_port),
            "SMTP_USER": "tester@example.com",
            "SMTP_PASSWORD": "tester-password",
            "SMTP_FROM_EMAIL": "tester@example.com",
            "SMTP_USE_SSL": "0",
            "SMTP_USE_TLS": "0",
            "PYTHONPATH": f"{sitecustomize_dir}:{env.get('PYTHONPATH', '')}".rstrip(":"),
        }
    )
    proc = subprocess.Popen(
        [str(ROOT_DIR / ".venv" / "bin" / "python"), "-m", "uvicorn", "exschool_game.app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    logs: list[str] = []

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            logs.append(line.rstrip("\n"))

    threading.Thread(target=reader, name="app-log-reader", daemon=True).start()
    return proc, logs


def stop_app_server(proc: subprocess.Popen[str] | None) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def make_sitecustomize_dir() -> Path:
    tmp_dir = Path(tempfile.mkdtemp(prefix="verify-auth-dom-"))
    (tmp_dir / "sitecustomize.py").write_text(
        dedent(
            """
            import smtplib

            def _noop_login(self, *args, **kwargs):
                return (235, b"2.7.0 Authentication successful")

            smtplib.SMTP.login = _noop_login
            smtplib.SMTP_SSL.login = _noop_login
            """
        ).lstrip(),
        encoding="utf-8",
    )
    return tmp_dir


def extract_code(message: str) -> str:
    match = re.search(r"验证码为：(\d{6})", message)
    if not match:
        raise RuntimeError(f"could not find verification code in message: {message!r}")
    return match.group(1)


async def run_browser(base_url: str, smtp_server: CaptureSMTPServer, headed: bool) -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=not headed,
            executable_path=CHROMIUM_PATH,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-gpu",
                "--disable-software-rasterizer",
            ],
        )
        try:
            context = None
            try:
                context = await browser.new_context()
                page = await context.new_page()
                console_errors: list[str] = []
                page_errors: list[str] = []
                page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                suffix = str(int(time.time() * 1000))
                name = f"pw-auth-{suffix}"
                email = f"{name}@example.com"
                password = "playwright-pass"

                await page.goto(f"{base_url}/auth?mode=register", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                await page.locator('script[src*="static/js/auth.js"]').wait_for(state="attached", timeout=PAGE_TIMEOUT_MS)
                if await page.locator("#send-code-button").count() != 1:
                    raise AssertionError("auth page did not render the send-code button")
                if await page.locator("#register-form").count() != 1:
                    raise AssertionError("auth page did not render the register form")
                if not await page.evaluate("Boolean(window.__EXSCHOOL_CSRF_TOKEN__)"):
                    raise AssertionError("base.js did not expose the CSRF token to the auth page")
                if await page.locator('input[name="_csrf"]').count() < 1:
                    raise AssertionError("base.js did not inject a CSRF field into the register form")

                await page.get_by_label("用户名").fill(name)
                await page.get_by_label("邮箱").fill(email)

                async with page.expect_response(lambda response: response.url.endswith("/auth/email-code") and response.request.method == "POST") as response_info:
                    await page.get_by_role("button", name="发送验证码").click(timeout=PAGE_TIMEOUT_MS)

                response = await response_info.value
                if response.status != 200:
                    raise AssertionError(f"send-code request failed with {response.status}")
                payload = await response.json()
                if payload.get("ok") is not True:
                    raise AssertionError(f"send-code response was not successful: {payload!r}")

                await page.locator("#code-status").wait_for(state="visible", timeout=PAGE_TIMEOUT_MS)
                status_text = (await page.locator("#code-status").text_content() or "").strip()
                if "验证码已发送" not in status_text:
                    raise AssertionError(f"unexpected send-code status text: {status_text!r}")
                button_text = (await page.locator("#send-code-button").text_content() or "").strip()
                if not re.fullmatch(r"\d+s", button_text):
                    raise AssertionError(f"send-code button did not enter cooldown: {button_text!r}")

                try:
                    mail = smtp_server.messages.get(timeout=10)
                except Exception as exc:
                    raise AssertionError("no verification email was captured from the browser-triggered send-code request") from exc
                code = extract_code(str(mail["data"]))

                await page.get_by_label("验证码").fill(code)
                await page.get_by_label("密码").fill(password)
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS):
                    await page.get_by_role("button", name="注册").click(timeout=PAGE_TIMEOUT_MS)

                if "mode=login" not in page.url or "registered=1" not in page.url:
                    raise AssertionError(f"register flow did not redirect to login with success parameters: {page.url!r}")
                if await page.locator("text=注册成功，请登录。").count() < 1:
                    raise AssertionError("register flow did not render the success message on the login page")
                account_value = await page.locator('input[name="account"]').input_value()
                if account_value != email:
                    raise AssertionError(f"login page did not prefill the registered account: {account_value!r}")

                await page.get_by_label("密码").fill(password)
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS):
                    await page.get_by_role("button", name="登录").click(timeout=PAGE_TIMEOUT_MS)

                if page.url.rstrip("/") != f"{base_url}".rstrip("/"):
                    raise AssertionError(f"login flow did not land on the home page: {page.url!r}")
                user_chip = page.locator(".site-user-chip strong")
                if await user_chip.count() < 1:
                    raise AssertionError("home page did not render the authenticated user chip")
                chip_name = (await user_chip.first.text_content() or "").strip()
                if chip_name != name:
                    raise AssertionError(f"authenticated chip name mismatch: {chip_name!r}")

                if page_errors:
                    raise AssertionError(f"page errors captured: {page_errors[:3]}")
                if console_errors:
                    raise AssertionError(f"console errors captured: {console_errors[:3]}")
                print(f"[ok] auth DOM flow completed for {email}")
            finally:
                if context is not None:
                    await context.close()
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the auth page JS path via a real browser DOM flow.")
    parser.add_argument("--headed", action="store_true", help="Run Chromium in headed mode.")
    args = parser.parse_args()

    smtp_server = None
    smtp_stop = None
    smtp_thread = None
    app_proc = None
    sitecustomize_dir = make_sitecustomize_dir()
    port = find_free_port()
    base_url = f"http://127.0.0.1:{port}"

    try:
        smtp_server, smtp_stop, smtp_thread, smtp_port = start_smtp_server()
        app_proc, logs = start_app_server(port, smtp_port, sitecustomize_dir)
        wait_for_server(base_url)
        try:
            asyncio.run(run_browser(base_url, smtp_server, args.headed))
        except Exception:
            if logs:
                print("\n".join(logs[-50:]), file=sys.stderr)
            raise
        return 0
    finally:
        stop_app_server(app_proc)
        if smtp_server and smtp_stop and smtp_thread:
            stop_smtp_server(smtp_server, smtp_stop, smtp_thread)


if __name__ == "__main__":
    raise SystemExit(main())
