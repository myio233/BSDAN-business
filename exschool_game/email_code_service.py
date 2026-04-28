from __future__ import annotations

import os
import secrets
import smtplib
import threading
import time
from dataclasses import dataclass
from email.message import EmailMessage


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER = os.environ.get("SMTP_USER", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", "").strip()
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Exschool Game").strip() or "Exschool Game"
SMTP_USE_SSL = _env_bool("SMTP_USE_SSL", True)
SMTP_USE_TLS = _env_bool("SMTP_USE_TLS", False)
EMAIL_CODE_EXPIRE_SECONDS = int(os.environ.get("EMAIL_CODE_EXPIRE_SECONDS", "300"))
EMAIL_CODE_RESEND_SECONDS = int(os.environ.get("EMAIL_CODE_RESEND_SECONDS", "90"))
MAIL_SITE_NAME = os.environ.get("EXSCHOOL_AUTH_SITE_NAME", "Exschool Game").strip() or "Exschool Game"


@dataclass(slots=True)
class CodeEntry:
    purpose: str
    code: str
    sent_at: float
    expires_at: float


class EmailCodeService:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.codes: dict[str, CodeEntry] = {}
        self.delivery_errors: dict[str, str] = {}

    @staticmethod
    def normalize_email(email: str) -> str:
        clean = email.strip().lower()
        if "@" not in clean:
            raise ValueError("邮箱格式不正确。")
        return clean

    @staticmethod
    def _ensure_enabled() -> None:
        if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
            raise ValueError("邮件服务未配置，请先填写 SMTP_HOST、SMTP_USER、SMTP_PASSWORD。")

    def _send_mail(self, to_email: str, subject: str, text: str) -> None:
        self._ensure_enabled()
        message = EmailMessage()
        from_email = SMTP_FROM_EMAIL or SMTP_USER
        message["From"] = f"{SMTP_FROM_NAME} <{from_email}>"
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text)

        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(message)
            return

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(message)

    def send_code(self, email: str, purpose: str) -> int:
        self._ensure_enabled()
        clean_email = self.normalize_email(email)
        now = time.time()
        action_text = "登录" if purpose == "login" else "注册"
        with self.lock:
            existing = self.codes.get(clean_email)
            if existing is not None:
                wait_seconds = EMAIL_CODE_RESEND_SECONDS - int(now - existing.sent_at)
                if wait_seconds > 0:
                    raise ValueError(f"发送过于频繁，请 {wait_seconds} 秒后重试。")
            code = f"{secrets.randbelow(1_000_000):06d}"
        subject = f"{MAIL_SITE_NAME} {action_text}验证码"
        body = (
            f"你好，\n\n"
            f"你的 {MAIL_SITE_NAME} {action_text}验证码为：{code}\n"
            f"验证码 {max(1, EMAIL_CODE_EXPIRE_SECONDS // 60)} 分钟内有效。\n"
            f"如果这不是你的操作，请忽略本邮件。\n"
        )
        try:
            self._send_mail(clean_email, subject, body)
        except Exception as exc:
            with self.lock:
                self.delivery_errors[clean_email] = str(exc)
            raise
        with self.lock:
            self.codes[clean_email] = CodeEntry(
                purpose=purpose,
                code=code,
                sent_at=now,
                expires_at=now + EMAIL_CODE_EXPIRE_SECONDS,
            )
            self.delivery_errors.pop(clean_email, None)
            return EMAIL_CODE_RESEND_SECONDS

    def verify_code(self, email: str, code: str, purpose: str) -> None:
        clean_email = self.normalize_email(email)
        clean_code = code.strip()
        if len(clean_code) != 6 or not clean_code.isdigit():
            raise ValueError("验证码应为 6 位数字。")
        with self.lock:
            item = self.codes.get(clean_email)
            if item is None:
                raise ValueError("验证码不存在或已失效，请重新发送。")
            if item.purpose != purpose:
                raise ValueError("验证码用途不匹配，请重新发送。")
            if time.time() > item.expires_at:
                self.codes.pop(clean_email, None)
                raise ValueError("验证码已过期，请重新发送。")
            if not secrets.compare_digest(item.code, clean_code):
                raise ValueError("验证码错误。")
            self.codes.pop(clean_email, None)


email_code_service = EmailCodeService()
