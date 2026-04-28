from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = BASE_DIR / "storage"
USER_STORE_PATH = STORAGE_DIR / "user_games.json"


class UserGameStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.users: dict[str, dict[str, Any]] = {}
        self.load_error: str | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _backup_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".bak")

    def _load_from_path(self, path: Path) -> dict[str, dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("对局存储格式无效。")
        users = payload.get("users", {})
        if not isinstance(users, dict):
            raise ValueError("对局存储缺少 users 映射。")
        return dict(users)

    def _load(self) -> None:
        if not self.path.exists():
            self.users = {}
            self.load_error = None
            return
        try:
            self.users = self._load_from_path(self.path)
            self.load_error = None
            return
        except Exception as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"
        backup_path = self._backup_path()
        if backup_path.exists():
            try:
                self.users = self._load_from_path(backup_path)
                self.load_error = None
                return
            except Exception:
                pass
        self.users = {}

    def _save(self) -> None:
        payload = {"users": self.users}
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + f".tmp-{os.getpid()}-{uuid4().hex}")
        tmp_path.write_text(raw, encoding="utf-8")
        backup_path = self._backup_path()
        if self.path.exists():
            shutil.copy2(self.path, backup_path)
        os.replace(tmp_path, self.path)
        self.load_error = None

    def _user_bucket(self, client: dict[str, Any]) -> dict[str, Any]:
        client_id = str(client.get("client_id", "")).strip()
        if not client_id:
            raise ValueError("用户标识不能为空。")
        bucket = self.users.setdefault(
            client_id,
            {
                "profile": {},
                "active_game_sessions": [],
                "history": [],
            },
        )
        self._normalize_bucket(bucket)
        bucket["profile"] = {
            "client_id": client_id,
            "name": str(client.get("name", "")).strip(),
            "email": str(client.get("email", "")).strip().lower(),
            "updated_at": time.time(),
        }
        return bucket

    def _normalize_bucket(self, bucket: dict[str, Any]) -> None:
        active_sessions = bucket.get("active_game_sessions")
        if isinstance(active_sessions, list):
            normalized = [dict(item) for item in active_sessions if isinstance(item, dict)]
        else:
            legacy_session = bucket.get("active_game_session")
            normalized = [dict(legacy_session)] if isinstance(legacy_session, dict) else []
        bucket["active_game_sessions"] = normalized
        bucket.pop("active_game_session", None)
        history = bucket.get("history")
        if not isinstance(history, list):
            bucket["history"] = []

    def _active_sessions(self, bucket: dict[str, Any]) -> list[dict[str, Any]]:
        self._normalize_bucket(bucket)
        active_sessions = bucket["active_game_sessions"]
        active_sessions.sort(key=lambda item: float(item.get("updated_at", 0.0) or 0.0), reverse=True)
        return active_sessions

    @staticmethod
    def _copy_payload(payload: Any) -> Any:
        return copy.deepcopy(payload)

    def get_active_game_session(self, client_id: str, game_id: str | None = None) -> dict[str, Any] | None:
        with self.lock:
            bucket = self.users.get(client_id)
            if not bucket:
                return None
            active_sessions = self._active_sessions(bucket)
            if game_id:
                for session in active_sessions:
                    if str(session.get("game_id", "")).strip() == game_id:
                        return self._copy_payload(session)
                return None
            return self._copy_payload(active_sessions[0]) if active_sessions else None

    def list_active_game_sessions(self, client_id: str) -> list[dict[str, Any]]:
        with self.lock:
            bucket = self.users.get(client_id)
            if not bucket:
                return []
            return [self._copy_payload(item) for item in self._active_sessions(bucket)]

    def list_history(self, client_id: str) -> list[dict[str, Any]]:
        with self.lock:
            bucket = self.users.get(client_id)
            if not bucket:
                return []
            self._normalize_bucket(bucket)
            history = bucket.get("history")
            if not isinstance(history, list):
                return []
            return [self._copy_payload(item) for item in history if isinstance(item, dict)]

    def get_history_game(self, client_id: str, game_id: str) -> dict[str, Any] | None:
        with self.lock:
            bucket = self.users.get(client_id)
            if not bucket:
                return None
            self._normalize_bucket(bucket)
            history = bucket.get("history")
            if not isinstance(history, list):
                return None
            for item in history:
                if isinstance(item, dict) and str(item.get("game_id", "")).strip() == game_id:
                    return self._copy_payload(item)
            return None

    def save_active_game_session(self, client: dict[str, Any], session: dict[str, Any]) -> None:
        with self.lock:
            bucket = self._user_bucket(client)
            active_sessions = self._active_sessions(bucket)
            session_payload = self._copy_payload(session)
            game_id = str(session_payload.get("game_id", "")).strip()
            if not game_id:
                raise ValueError("进行中对局缺少 game_id。")
            session_payload["updated_at"] = time.time()
            replaced = False
            for idx, existing in enumerate(active_sessions):
                if str(existing.get("game_id", "")).strip() == game_id:
                    active_sessions[idx] = session_payload
                    replaced = True
                    break
            if not replaced:
                active_sessions.append(session_payload)
            active_sessions.sort(key=lambda item: float(item.get("updated_at", 0.0) or 0.0), reverse=True)
            self._save()

    def clear_active_game_session(self, client_id: str, game_id: str | None = None) -> None:
        with self.lock:
            bucket = self.users.get(client_id)
            if not bucket:
                return
            active_sessions = self._active_sessions(bucket)
            if game_id:
                bucket["active_game_sessions"] = [
                    item for item in active_sessions if str(item.get("game_id", "")).strip() != game_id
                ]
            else:
                bucket["active_game_sessions"] = []
            self._save()

    def archive_completed_game(self, client: dict[str, Any], session: dict[str, Any], reports: list[dict[str, Any]]) -> None:
        with self.lock:
            bucket = self._user_bucket(client)
            active_sessions = self._active_sessions(bucket)
            history = bucket.setdefault("history", [])
            summary = {
                "game_id": str(session.get("game_id", "")),
                "company_name": str(session.get("company_name", "")),
                "home_city": str(session.get("home_city", "")),
                "single_player_mode": str(session.get("single_player_mode", "")),
                "completed_at": time.time(),
                "reports": reports,
                "all_company_rounds": list(session.get("all_company_rounds", [])) if isinstance(session.get("all_company_rounds"), list) else [],
                "final_net_assets": float(reports[-1].get("net_assets", 0.0) or 0.0) if reports else 0.0,
            }
            history.insert(0, summary)
            del history[10:]
            bucket["active_game_sessions"] = [
                item
                for item in active_sessions
                if str(item.get("game_id", "")).strip() != str(session.get("game_id", "")).strip()
            ]
            self._save()


user_game_store = UserGameStore(USER_STORE_PATH)
