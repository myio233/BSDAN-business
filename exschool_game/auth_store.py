from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = BASE_DIR / "storage"
AUTH_USERS_PATH = STORAGE_DIR / "auth_users.json"


@dataclass(slots=True)
class AuthAccount:
    client_id: str
    name: str
    email: str
    password_salt: str
    password_hash: str
    enabled: bool
    auth_token: str | None
    auth_token_created_at: float | None
    created_at: float
    updated_at: float

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "AuthAccount":
        return cls(
            client_id=str(payload.get("client_id", "")),
            name=str(payload.get("name", "")),
            email=str(payload.get("email", "")),
            password_salt=str(payload.get("password_salt", "")),
            password_hash=str(payload.get("password_hash", "")),
            enabled=bool(payload.get("enabled", True)),
            auth_token=str(payload["auth_token"]) if payload.get("auth_token") else None,
            auth_token_created_at=float(payload["auth_token_created_at"]) if payload.get("auth_token_created_at") is not None else None,
            created_at=float(payload.get("created_at", 0.0) or 0.0),
            updated_at=float(payload.get("updated_at", 0.0) or 0.0),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "name": self.name,
            "email": self.email,
            "password_salt": self.password_salt,
            "password_hash": self.password_hash,
            "enabled": self.enabled,
            "auth_token": self.auth_token,
            "auth_token_created_at": self.auth_token_created_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class AuthStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.RLock()
        self.accounts: dict[str, AuthAccount] = {}
        self.load_error: str | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _backup_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".bak")

    def _load_from_path(self, path: Path) -> dict[str, AuthAccount]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("账号存储格式无效。")
        return {
            client_id: AuthAccount.from_payload(payload)
            for client_id, payload in raw.items()
            if isinstance(payload, dict)
        }

    def _load(self) -> None:
        if not self.path.exists():
            self.accounts = {}
            self.load_error = None
            return
        try:
            self.accounts = self._load_from_path(self.path)
            self.load_error = None
            return
        except Exception as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"
        backup_path = self._backup_path()
        if backup_path.exists():
            try:
                self.accounts = self._load_from_path(backup_path)
                self.load_error = None
                return
            except Exception:
                pass
        self.accounts = {}

    def _save(self) -> None:
        payload = {client_id: account.to_payload() for client_id, account in self.accounts.items()}
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + f".tmp-{os.getpid()}-{uuid4().hex}")
        tmp_path.write_text(raw, encoding="utf-8")
        backup_path = self._backup_path()
        if self.path.exists():
            shutil.copy2(self.path, backup_path)
        os.replace(tmp_path, self.path)
        self.load_error = None

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()

    @staticmethod
    def _public(account: AuthAccount) -> dict[str, str]:
        return {
            "client_id": account.client_id,
            "name": account.name,
            "email": account.email,
        }

    def _find_by_email(self, email: str) -> AuthAccount | None:
        clean_email = email.strip().lower()
        for account in self.accounts.values():
            if account.email.strip().lower() == clean_email:
                return account
        return None

    def _find_by_name(self, name: str) -> AuthAccount | None:
        clean_name = name.strip().lower()
        for account in self.accounts.values():
            if account.name.strip().lower() == clean_name:
                return account
        return None

    def get_public_by_id(self, client_id: str) -> dict[str, str] | None:
        with self.lock:
            account = self.accounts.get(client_id)
            if account is None or not account.enabled:
                return None
            return self._public(account)

    def get_public_by_id_and_token(self, client_id: str, token: str) -> dict[str, str] | None:
        clean_token = token.strip()
        if not clean_token:
            return None
        with self.lock:
            account = self.accounts.get(client_id)
            if account is None or not account.enabled:
                return None
            if not account.auth_token or not secrets.compare_digest(str(account.auth_token), clean_token):
                return None
            return self._public(account)

    def get_public_by_session(self, client_id: str, auth_token: str) -> dict[str, str] | None:
        clean_client_id = client_id.strip()
        clean_auth_token = auth_token.strip()
        if not clean_client_id or not clean_auth_token:
            return None
        with self.lock:
            account = self.accounts.get(clean_client_id)
            if account is None or not account.enabled or not account.auth_token:
                return None
            if not secrets.compare_digest(str(account.auth_token), clean_auth_token):
                return None
            return self._public(account)

    def get_public_by_email(self, email: str) -> dict[str, str] | None:
        with self.lock:
            account = self._find_by_email(email)
            if account is None or not account.enabled:
                return None
            return self._public(account)

    def register_user(self, name: str, email: str, password: str) -> tuple[dict[str, str], str]:
        clean_name = name.strip()
        clean_email = email.strip().lower()
        if not clean_name:
            raise ValueError("用户名不能为空。")
        if len(clean_name) < 2:
            raise ValueError("用户名至少 2 位。")
        if "@" not in clean_email:
            raise ValueError("邮箱格式不正确。")
        if len(password) < 6:
            raise ValueError("密码至少 6 位。")
        with self.lock:
            if self._find_by_name(clean_name) is not None:
                raise ValueError("该用户名已被使用。")
            if self._find_by_email(clean_email) is not None:
                raise ValueError("该邮箱已注册。")
            now = time.time()
            salt = secrets.token_hex(16)
            account = AuthAccount(
                client_id=f"user_{secrets.token_hex(12)}",
                name=clean_name,
                email=clean_email,
                password_salt=salt,
                password_hash=self._hash_password(password, salt),
                enabled=True,
                auth_token=f"portal_{secrets.token_urlsafe(24)}",
                auth_token_created_at=now,
                created_at=now,
                updated_at=now,
            )
            self.accounts[account.client_id] = account
            self._save()
            return self._public(account), str(account.auth_token)

    def login_user(self, account: str, password: str) -> tuple[dict[str, str], str]:
        identifier = account.strip()
        if not identifier:
            raise ValueError("请输入用户名或邮箱。")
        normalized = identifier.lower()
        with self.lock:
            user = self._find_by_email(normalized) if "@" in normalized else self._find_by_name(normalized)
            if user is None:
                raise ValueError("账号不存在。")
            if not user.enabled:
                raise ValueError("账号已被禁用。")
            hashed = self._hash_password(password, user.password_salt)
            if not secrets.compare_digest(hashed, user.password_hash):
                raise ValueError("密码错误。")
            user.auth_token = f"portal_{secrets.token_urlsafe(24)}"
            user.auth_token_created_at = time.time()
            user.updated_at = time.time()
            self._save()
            return self._public(user), str(user.auth_token)


auth_store = AuthStore(AUTH_USERS_PATH)
