from __future__ import annotations

import os
from dataclasses import dataclass

from .auth_store import auth_store
from .email_code_service import EMAIL_CODE_RESEND_SECONDS, email_code_service
from .request_guard_service import request_guard_service


AUTH_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("AUTH_RATE_LIMIT_WINDOW_SECONDS", "600"))
AUTH_RATE_LIMIT_BLOCK_SECONDS = int(os.environ.get("AUTH_RATE_LIMIT_BLOCK_SECONDS", "900"))
AUTH_RATE_LIMIT_IP_ATTEMPTS = int(os.environ.get("AUTH_RATE_LIMIT_IP_ATTEMPTS", "20"))
AUTH_RATE_LIMIT_IDENTITY_ATTEMPTS = int(os.environ.get("AUTH_RATE_LIMIT_IDENTITY_ATTEMPTS", "8"))
REGISTER_RATE_LIMIT_IP_ATTEMPTS = int(os.environ.get("REGISTER_RATE_LIMIT_IP_ATTEMPTS", "6"))
EMAIL_SEND_RATE_LIMIT_IP_ATTEMPTS = int(os.environ.get("EMAIL_SEND_RATE_LIMIT_IP_ATTEMPTS", "6"))
EMAIL_SEND_RATE_LIMIT_IDENTITY_ATTEMPTS = int(os.environ.get("EMAIL_SEND_RATE_LIMIT_IDENTITY_ATTEMPTS", "3"))


class AuthClientError(ValueError):
    pass


class AuthServiceUnavailableError(AuthClientError):
    pass


@dataclass(slots=True)
class AuthUser:
    client_id: str
    name: str
    email: str

    @classmethod
    def from_payload(cls, payload: dict[str, str]) -> "AuthUser":
        return cls(
            client_id=str(payload.get("client_id", "")),
            name=str(payload.get("name", "")),
            email=str(payload.get("email", "")),
        )

    def to_session(self) -> dict[str, str]:
        return {
            "client_id": self.client_id,
            "name": self.name,
            "email": self.email,
        }


@dataclass(slots=True)
class AuthResult:
    token: str
    user: AuthUser


def _enforce_guard(
    *,
    scope: str,
    identity: str,
    limit: int,
    message: str,
    window_seconds: int | None = None,
    block_seconds: int | None = None,
) -> None:
    request_guard_service.enforce(
        scope=scope,
        identity=identity,
        limit=limit,
        window_seconds=window_seconds or AUTH_RATE_LIMIT_WINDOW_SECONDS,
        block_seconds=block_seconds or AUTH_RATE_LIMIT_BLOCK_SECONDS,
        message=message,
    )


def send_email_code(email: str, purpose: str = "register", *, client_ip: str = "") -> int | None:
    clean_email = email.strip().lower()
    try:
        _enforce_guard(
            scope="portal_email_send_ip",
            identity=client_ip,
            limit=EMAIL_SEND_RATE_LIMIT_IP_ATTEMPTS,
            message="验证码发送过于频繁。",
        )
        _enforce_guard(
            scope=f"portal_email_send_{purpose}",
            identity=clean_email,
            limit=EMAIL_SEND_RATE_LIMIT_IDENTITY_ATTEMPTS,
            message="该邮箱验证码发送过于频繁。",
        )
        if purpose == "register":
            if auth_store.get_public_by_email(clean_email):
                raise ValueError("该邮箱已注册，请直接登录。")
        elif not auth_store.get_public_by_email(clean_email):
            raise ValueError("账号不存在，请先注册。")
        return email_code_service.send_code(clean_email, purpose)
    except ValueError as exc:
        raise AuthClientError(str(exc)) from exc
    except Exception as exc:
        raise AuthServiceUnavailableError("验证码发送失败，请稍后重试。") from exc


def register_user(name: str, email: str, code: str, password: str, *, client_ip: str = "") -> AuthResult:
    clean_name = name.strip().lower()
    clean_email = email.strip().lower()
    try:
        _enforce_guard(
            scope="portal_register_ip",
            identity=client_ip,
            limit=REGISTER_RATE_LIMIT_IP_ATTEMPTS,
            message="注册请求过于频繁。",
        )
        _enforce_guard(
            scope="portal_register_email",
            identity=clean_email,
            limit=AUTH_RATE_LIMIT_IDENTITY_ATTEMPTS,
            message="该邮箱注册尝试过于频繁。",
        )
        _enforce_guard(
            scope="portal_register_name",
            identity=clean_name,
            limit=AUTH_RATE_LIMIT_IDENTITY_ATTEMPTS,
            message="该用户名注册尝试过于频繁。",
        )
        _enforce_guard(
            scope="portal_register_code_email",
            identity=clean_email,
            limit=AUTH_RATE_LIMIT_IDENTITY_ATTEMPTS,
            message="该邮箱验证码校验过于频繁。",
        )
        email_code_service.verify_code(clean_email, code, "register")
        user_payload, token = auth_store.register_user(name, clean_email, password)
        request_guard_service.reset(scope="portal_register_code_email", identity=clean_email)
        return AuthResult(token=token, user=AuthUser.from_payload(user_payload))
    except ValueError as exc:
        raise AuthClientError(str(exc)) from exc


def login_user(account: str, password: str, *, client_ip: str = "") -> AuthResult:
    identifier = account.strip().lower()
    try:
        _enforce_guard(
            scope="portal_login_ip",
            identity=client_ip,
            limit=AUTH_RATE_LIMIT_IP_ATTEMPTS,
            message="登录请求过于频繁。",
        )
        _enforce_guard(
            scope="portal_login_account",
            identity=identifier,
            limit=AUTH_RATE_LIMIT_IDENTITY_ATTEMPTS,
            message="该账号登录尝试过于频繁。",
        )
        user_payload, token = auth_store.login_user(account, password)
        request_guard_service.reset(scope="portal_login_account", identity=identifier)
        return AuthResult(token=token, user=AuthUser.from_payload(user_payload))
    except ValueError as exc:
        raise AuthClientError(str(exc)) from exc


def get_user_by_id(client_id: str) -> dict[str, str] | None:
    return auth_store.get_public_by_id(client_id)


def get_user_by_session(client_id: str, token: str) -> dict[str, str] | None:
    return auth_store.get_public_by_id_and_token(client_id, token)
