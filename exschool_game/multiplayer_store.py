from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parents[1]
STORAGE_DIR = BASE_DIR / "storage"
MULTIPLAYER_ROOM_STORE_PATH = STORAGE_DIR / "multiplayer_rooms.json"
ROOM_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
ROOM_CODE_LENGTH = 6
DEFAULT_ROOM_SEAT_COUNT = 6


class MultiplayerRoomStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.rooms: dict[str, dict[str, Any]] = {}
        self.load_error: str | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def _backup_path(self) -> Path:
        return self.path.with_suffix(self.path.suffix + ".bak")

    def _load_from_path(self, path: Path) -> dict[str, dict[str, Any]]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("房间存储格式无效。")
        rooms = payload.get("rooms", {})
        if not isinstance(rooms, dict):
            raise ValueError("房间存储缺少 rooms 映射。")
        normalized: dict[str, dict[str, Any]] = {}
        for room_id, room in rooms.items():
            if not isinstance(room, dict):
                continue
            normalized_room = self._normalize_room(dict(room), fallback_room_id=str(room_id).strip())
            if normalized_room is not None:
                normalized[normalized_room["room_id"]] = normalized_room
        return normalized

    def _load(self) -> None:
        if not self.path.exists():
            self.rooms = {}
            self.load_error = None
            return
        try:
            self.rooms = self._load_from_path(self.path)
            self.load_error = None
            return
        except Exception as exc:
            self.load_error = f"{type(exc).__name__}: {exc}"
        backup_path = self._backup_path()
        if backup_path.exists():
            try:
                self.rooms = self._load_from_path(backup_path)
                self.load_error = None
                return
            except Exception:
                pass
        self.rooms = {}

    def _save(self) -> None:
        payload = {"rooms": self.rooms}
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        tmp_path = self.path.with_suffix(self.path.suffix + f".tmp-{os.getpid()}-{uuid4().hex}")
        tmp_path.write_text(raw, encoding="utf-8")
        backup_path = self._backup_path()
        if self.path.exists():
            shutil.copy2(self.path, backup_path)
        os.replace(tmp_path, self.path)
        self.load_error = None

    @staticmethod
    def _copy_payload(payload: Any) -> Any:
        return copy.deepcopy(payload)

    @staticmethod
    def _clean_str(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_client(client: dict[str, Any]) -> dict[str, str]:
        client_id = str(client.get("client_id", "")).strip()
        if not client_id:
            raise ValueError("用户标识不能为空。")
        return {
            "client_id": client_id,
            "name": str(client.get("name", "")).strip(),
            "email": str(client.get("email", "")).strip().lower(),
        }

    @staticmethod
    def _coerce_non_negative_int(value: object, *, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(parsed, 0)

    @staticmethod
    def _coerce_float(value: object, *, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _first_available_seat_index(cls, used_seat_indexes: set[int]) -> int:
        seat_index = 0
        while seat_index in used_seat_indexes:
            seat_index += 1
        return seat_index

    @classmethod
    def _normalize_member(
        cls,
        member: dict[str, Any],
        *,
        default_joined_at: float,
        used_seat_indexes: set[int],
    ) -> dict[str, Any]:
        profile = cls._normalize_client(member)
        joined_at = cls._coerce_float(member.get("joined_at"), default=default_joined_at)
        updated_at = cls._coerce_float(member.get("updated_at"), default=joined_at)
        seat_index = cls._coerce_non_negative_int(
            member.get("seat_index"),
            default=cls._first_available_seat_index(used_seat_indexes),
        )
        while seat_index in used_seat_indexes:
            seat_index += 1
        used_seat_indexes.add(seat_index)
        latest_report_detail = member.get("latest_report_detail")
        return {
            "client_id": profile["client_id"],
            "name": profile["name"],
            "email": profile["email"],
            "home_city": cls._clean_str(member.get("home_city")) or "",
            "is_ready": bool(member.get("is_ready", False)),
            "ready": bool(member.get("ready", member.get("is_ready", False))),
            "joined_at": joined_at,
            "updated_at": updated_at,
            "seat_index": seat_index,
            "team_id": cls._clean_str(member.get("team_id")) or None,
            "reports": list(member.get("reports", [])) if isinstance(member.get("reports"), list) else [],
            "all_company_rounds": (
                list(member.get("all_company_rounds", []))
                if isinstance(member.get("all_company_rounds"), list)
                else []
            ),
            "latest_report_detail": dict(latest_report_detail) if isinstance(latest_report_detail, dict) else None,
            "submitted_round_ids": (
                [cls._clean_str(item) for item in member.get("submitted_round_ids", []) if cls._clean_str(item)]
                if isinstance(member.get("submitted_round_ids"), list)
                else []
            ),
        }

    @staticmethod
    def _sorted_members(room: dict[str, Any]) -> list[dict[str, Any]]:
        members = room.get("members", [])
        if not isinstance(members, list):
            return []
        return sorted(
            [dict(member) for member in members if isinstance(member, dict)],
            key=lambda item: (
                float(item.get("joined_at", 0.0) or 0.0),
                int(item.get("seat_index", 0) or 0),
                str(item.get("client_id", "")),
            ),
        )

    def _normalize_room(self, room: dict[str, Any], *, fallback_room_id: str = "") -> dict[str, Any] | None:
        room_id = self._clean_str(room.get("room_id")) or fallback_room_id or f"room_{uuid4().hex}"
        raw_members = room.get("members")
        if not isinstance(raw_members, list):
            raw_members = []
        now = time.time()
        used_seat_indexes: set[int] = set()
        members: list[dict[str, Any]] = []
        seen_client_ids: set[str] = set()
        for raw_member in raw_members:
            if not isinstance(raw_member, dict):
                continue
            try:
                member = self._normalize_member(raw_member, default_joined_at=now, used_seat_indexes=used_seat_indexes)
            except ValueError:
                continue
            if member["client_id"] in seen_client_ids:
                continue
            seen_client_ids.add(member["client_id"])
            members.append(member)
        if not members:
            return None
        members.sort(key=lambda item: (int(item["seat_index"]), float(item["joined_at"]), item["client_id"]))
        requested_bot_count = self._coerce_non_negative_int(room.get("bot_count"), default=0)
        minimum_seat_count = max(len(members) + requested_bot_count, 1)
        requested_seat_count = self._coerce_non_negative_int(room.get("seat_count"), default=minimum_seat_count)
        seat_count = max(requested_seat_count, minimum_seat_count)
        host_client_id = self._clean_str(room.get("host_client_id"))
        member_ids = {member["client_id"] for member in members}
        if host_client_id not in member_ids:
            host_client_id = min(members, key=lambda item: (float(item["joined_at"]), item["seat_index"]))["client_id"]
        created_at = self._coerce_float(room.get("created_at"), default=now)
        updated_at = self._coerce_float(room.get("updated_at"), default=created_at)
        metadata = room.get("metadata")
        current_round = self._clean_str(room.get("current_round")) or "r1"
        round_started = room.get("round_started_at_ms_by_round")
        if not isinstance(round_started, dict):
            round_started = {}
        normalized_round_started: dict[str, int] = {}
        for round_id, value in round_started.items():
            try:
                normalized_round_started[self._clean_str(round_id).lower()] = int(value)
            except (TypeError, ValueError):
                continue
        pending_submissions = room.get("pending_submissions")
        if not isinstance(pending_submissions, dict):
            pending_submissions = {}
        normalized_pending: dict[str, dict[str, Any]] = {}
        for round_id, submissions in pending_submissions.items():
            if not isinstance(submissions, dict):
                continue
            normalized_pending[self._clean_str(round_id).lower()] = {
                self._clean_str(team_id): dict(payload)
                for team_id, payload in submissions.items()
                if isinstance(payload, dict)
            }
        team_states = room.get("team_states")
        latest_reports_by_team = room.get("latest_reports_by_team")
        human_team_order = room.get("human_team_order")
        bot_team_order = room.get("bot_team_order")
        return {
            "room_id": room_id,
            "room_code": self._clean_str(room.get("room_code")).upper() or self._generate_room_code(),
            "room_name": self._clean_str(room.get("room_name") or room.get("name")),
            "host_client_id": host_client_id,
            "seat_count": seat_count,
            "bot_count": min(requested_bot_count, max(seat_count - len(members), 0)),
            "status": self._clean_str(room.get("status")) or "waiting",
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            "current_round": current_round,
            "round_started_at_ms_by_round": normalized_round_started,
            "pending_submissions": normalized_pending,
            "team_states": dict(team_states) if isinstance(team_states, dict) else {},
            "latest_reports_by_team": dict(latest_reports_by_team) if isinstance(latest_reports_by_team, dict) else {},
            "latest_round_id": self._clean_str(room.get("latest_round_id")).lower() or None,
            "human_team_order": [self._clean_str(item) for item in human_team_order] if isinstance(human_team_order, list) else [],
            "bot_team_order": [self._clean_str(item) for item in bot_team_order] if isinstance(bot_team_order, list) else [],
            "time_limit_seconds": self._coerce_non_negative_int(
                room.get("time_limit_seconds", (metadata or {}).get("time_limit_seconds")),
                default=40 * 60,
            ),
            "created_at": created_at,
            "updated_at": updated_at,
            "members": members,
        }

    def _generate_room_code(self) -> str:
        existing_codes = {str(room.get("room_code", "")).upper() for room in self.rooms.values() if isinstance(room, dict)}
        while True:
            code = "".join(secrets.choice(ROOM_CODE_ALPHABET) for _ in range(ROOM_CODE_LENGTH))
            if code not in existing_codes:
                return code

    def _resolve_room(self, room_ref: str) -> dict[str, Any] | None:
        clean_ref = self._clean_str(room_ref)
        if not clean_ref:
            return None
        room = self.rooms.get(clean_ref)
        if room is not None:
            return room
        upper_ref = clean_ref.upper()
        for candidate in self.rooms.values():
            if str(candidate.get("room_code", "")).upper() == upper_ref:
                return candidate
        return None

    def _require_room(self, room_ref: str) -> dict[str, Any]:
        room = self._resolve_room(room_ref)
        if room is None:
            raise ValueError("房间不存在。")
        return room

    @staticmethod
    def _member_index(room: dict[str, Any], client_id: str) -> int | None:
        members = room.get("members", [])
        if not isinstance(members, list):
            return None
        for index, member in enumerate(members):
            if isinstance(member, dict) and str(member.get("client_id", "")).strip() == client_id:
                return index
        return None

    @staticmethod
    def _next_host_client_id(room: dict[str, Any]) -> str:
        members = MultiplayerRoomStore._sorted_members(room)
        if not members:
            return ""
        return members[0]["client_id"]

    @staticmethod
    def _room_snapshot(room: dict[str, Any]) -> dict[str, Any]:
        members = MultiplayerRoomStore._sorted_members(room)
        host_client_id = str(room.get("host_client_id", "")).strip()
        members_for_snapshot = []
        member_by_seat: dict[int, dict[str, Any]] = {}
        for member in members:
            snapshot_member = {
                "client_id": member["client_id"],
                "name": member["name"],
                "email": member["email"],
                "is_ready": bool(member.get("is_ready", False)),
                "is_host": member["client_id"] == host_client_id,
                "joined_at": float(member.get("joined_at", 0.0) or 0.0),
                "updated_at": float(member.get("updated_at", 0.0) or 0.0),
                "seat_index": int(member.get("seat_index", 0) or 0),
            }
            members_for_snapshot.append(snapshot_member)
            member_by_seat[snapshot_member["seat_index"]] = snapshot_member

        seat_count = int(room.get("seat_count", 0) or 0)
        bot_count = int(room.get("bot_count", 0) or 0)
        bot_seat_indexes: set[int] = set()
        next_open_seat = 0
        while len(bot_seat_indexes) < bot_count and next_open_seat < seat_count:
            if next_open_seat not in member_by_seat:
                bot_seat_indexes.add(next_open_seat)
            next_open_seat += 1

        seats = []
        bot_ordinal = 0
        for seat_index in range(seat_count):
            member = member_by_seat.get(seat_index)
            if member is not None:
                seats.append({"seat_index": seat_index, "slot_type": "human", "occupied": True, **copy.deepcopy(member)})
                continue
            if seat_index in bot_seat_indexes:
                bot_ordinal += 1
                seats.append(
                    {
                        "seat_index": seat_index,
                        "slot_type": "bot",
                        "occupied": True,
                        "placeholder": True,
                        "client_id": "",
                        "name": f"Bot {bot_ordinal}",
                        "email": "",
                        "is_ready": True,
                        "is_host": False,
                        "joined_at": None,
                        "updated_at": None,
                    }
                )
                continue
            seats.append(
                {
                    "seat_index": seat_index,
                    "slot_type": "open",
                    "occupied": False,
                    "placeholder": True,
                    "client_id": "",
                    "name": "",
                    "email": "",
                    "is_ready": False,
                    "is_host": False,
                    "joined_at": None,
                    "updated_at": None,
                }
            )

        ready_count = sum(1 for member in members_for_snapshot if member["is_ready"])
        human_count = len(members_for_snapshot)
        open_seat_count = max(seat_count - human_count - bot_count, 0)
        return {
            "room_id": str(room.get("room_id", "")),
            "room_code": str(room.get("room_code", "")),
            "room_name": str(room.get("room_name", "")),
            "host_client_id": host_client_id,
            "seat_count": seat_count,
            "bot_count": bot_count,
            "human_count": human_count,
            "ready_count": ready_count,
            "open_seat_count": open_seat_count,
            "all_ready": bool(human_count) and ready_count == human_count,
            "status": str(room.get("status", "")),
            "metadata": copy.deepcopy(room.get("metadata", {})),
            "created_at": float(room.get("created_at", 0.0) or 0.0),
            "updated_at": float(room.get("updated_at", 0.0) or 0.0),
            "members": members_for_snapshot,
            "seats": seats,
        }

    def list_rooms(self) -> list[dict[str, Any]]:
        with self.lock:
            ordered_rooms = sorted(
                self.rooms.values(),
                key=lambda item: float(item.get("updated_at", 0.0) or 0.0),
                reverse=True,
            )
            return [self._copy_payload(self._room_snapshot(room)) for room in ordered_rooms]

    def list_rooms_for_user(self, client_id: str) -> list[dict[str, Any]]:
        clean_client_id = self._clean_str(client_id)
        with self.lock:
            rooms = [
                self._copy_payload(self._normalize_room(dict(room), fallback_room_id=str(room.get("room_id", ""))))
                for room in self.rooms.values()
                if any(
                    isinstance(member, dict) and self._clean_str(member.get("client_id")) == clean_client_id
                    for member in room.get("members", [])
                )
            ]
        rooms = [room for room in rooms if room is not None]
        rooms.sort(key=lambda item: float(item.get("updated_at", 0.0) or 0.0), reverse=True)
        return rooms

    def get_room(self, room_ref: str) -> dict[str, Any] | None:
        with self.lock:
            room = self._resolve_room(room_ref)
            if room is None:
                return None
            return self._copy_payload(self._room_snapshot(room))

    def get_room_raw(self, room_ref: str) -> dict[str, Any] | None:
        with self.lock:
            room = self._resolve_room(room_ref)
            if room is None:
                return None
            normalized_room = self._normalize_room(dict(room), fallback_room_id=str(room.get("room_id", "")))
            if normalized_room is None:
                return None
            return self._copy_payload(normalized_room)

    def create_room(
        self,
        host: dict[str, Any],
        *,
        room_name: str = "",
        seat_count: int = DEFAULT_ROOM_SEAT_COUNT,
        seat_limit: int | None = None,
        bot_count: int = 0,
        metadata: dict[str, Any] | None = None,
        status: str = "waiting",
        time_limit_seconds: int | None = None,
    ) -> dict[str, Any]:
        host_profile = self._normalize_client(host)
        requested_seat_count = seat_limit if seat_limit is not None else seat_count
        clean_seat_count = max(int(requested_seat_count), 1)
        clean_bot_count = max(int(bot_count), 0)
        if clean_bot_count >= clean_seat_count:
            raise ValueError("机器人数量必须小于房间座位数。")
        with self.lock:
            now = time.time()
            room_id = f"room_{uuid4().hex}"
            room = {
                "room_id": room_id,
                "room_code": self._generate_room_code(),
                "room_name": self._clean_str(room_name),
                "host_client_id": host_profile["client_id"],
                "seat_count": clean_seat_count,
                "bot_count": clean_bot_count,
                "status": self._clean_str(status) or "waiting",
                "metadata": dict(metadata) if isinstance(metadata, dict) else {},
                "time_limit_seconds": int(time_limit_seconds or (metadata or {}).get("time_limit_seconds") or 40 * 60),
                "current_round": "r1",
                "round_started_at_ms_by_round": {},
                "pending_submissions": {},
                "team_states": {},
                "latest_reports_by_team": {},
                "latest_round_id": None,
                "human_team_order": [],
                "bot_team_order": [],
                "created_at": now,
                "updated_at": now,
                "members": [
                    {
                        **host_profile,
                        "is_ready": False,
                        "joined_at": now,
                        "updated_at": now,
                        "seat_index": 0,
                    }
                ],
            }
            normalized_room = self._normalize_room(room, fallback_room_id=room_id)
            if normalized_room is None:
                raise ValueError("创建房间失败。")
            self.rooms[room_id] = normalized_room
            self._save()
            return self._copy_payload(self._room_snapshot(normalized_room))

    def save_room(self, room: dict[str, Any]) -> dict[str, Any]:
        normalized_room = self._normalize_room(dict(room), fallback_room_id=self._clean_str(room.get("room_id")))
        if normalized_room is None:
            raise ValueError("房间不存在。")
        normalized_room["updated_at"] = time.time()
        with self.lock:
            self.rooms[normalized_room["room_id"]] = normalized_room
            self._save()
        return self._copy_payload(normalized_room)

    def join_room(self, room_ref: str, client: dict[str, Any]) -> dict[str, Any]:
        profile = self._normalize_client(client)
        with self.lock:
            room = self._require_room(room_ref)
            member_index = self._member_index(room, profile["client_id"])
            now = time.time()
            if member_index is not None:
                existing = dict(room["members"][member_index])
                existing.update(profile)
                existing["updated_at"] = now
                room["members"][member_index] = existing
            else:
                human_count = len(room["members"])
                seat_count = int(room.get("seat_count", 0) or 0)
                bot_count = int(room.get("bot_count", 0) or 0)
                if human_count + bot_count >= seat_count:
                    raise ValueError("房间已满。")
                used_seat_indexes = {
                    int(member.get("seat_index", 0) or 0)
                    for member in room["members"]
                    if isinstance(member, dict)
                }
                room["members"].append(
                    {
                        **profile,
                        "is_ready": False,
                        "joined_at": now,
                        "updated_at": now,
                        "seat_index": self._first_available_seat_index(used_seat_indexes),
                    }
                )
            room["updated_at"] = now
            normalized_room = self._normalize_room(room, fallback_room_id=str(room.get("room_id", "")))
            if normalized_room is None:
                raise ValueError("房间不存在。")
            self.rooms[normalized_room["room_id"]] = normalized_room
            self._save()
            return self._copy_payload(self._room_snapshot(normalized_room))

    def leave_room(self, room_ref: str, client_id: str) -> dict[str, Any] | None:
        clean_client_id = self._clean_str(client_id)
        if not clean_client_id:
            raise ValueError("用户标识不能为空。")
        with self.lock:
            room = self._require_room(room_ref)
            remaining_members = [
                member
                for member in room["members"]
                if isinstance(member, dict) and str(member.get("client_id", "")).strip() != clean_client_id
            ]
            if len(remaining_members) == len(room["members"]):
                return self._copy_payload(self._room_snapshot(room))
            if not remaining_members:
                self.rooms.pop(str(room.get("room_id", "")), None)
                self._save()
                return None
            room["members"] = remaining_members
            room["host_client_id"] = self._next_host_client_id(room)
            room["updated_at"] = time.time()
            normalized_room = self._normalize_room(room, fallback_room_id=str(room.get("room_id", "")))
            if normalized_room is None:
                self.rooms.pop(str(room.get("room_id", "")), None)
                self._save()
                return None
            self.rooms[normalized_room["room_id"]] = normalized_room
            self._save()
            return self._copy_payload(self._room_snapshot(normalized_room))

    def update_room(self, room_ref: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("房间更新内容必须是对象。")
        allowed_keys = {"room_name", "seat_count", "bot_count", "status", "metadata", "host_client_id"}
        unknown_keys = sorted(key for key in patch if key not in allowed_keys)
        if unknown_keys:
            raise ValueError(f"房间更新字段不支持: {', '.join(unknown_keys)}")
        with self.lock:
            room = self._require_room(room_ref)
            updated_room = self._copy_payload(room)
            if "room_name" in patch:
                updated_room["room_name"] = self._clean_str(patch.get("room_name"))
            if "status" in patch:
                updated_room["status"] = self._clean_str(patch.get("status")) or updated_room.get("status", "waiting")
            if "metadata" in patch:
                metadata = patch.get("metadata")
                updated_room["metadata"] = dict(metadata) if isinstance(metadata, dict) else {}
            if "host_client_id" in patch:
                host_client_id = self._clean_str(patch.get("host_client_id"))
                member_ids = {
                    str(member.get("client_id", "")).strip()
                    for member in updated_room["members"]
                    if isinstance(member, dict)
                }
                if host_client_id and host_client_id not in member_ids:
                    raise ValueError("房主必须是房间成员。")
                if host_client_id:
                    updated_room["host_client_id"] = host_client_id
            seat_count = int(updated_room.get("seat_count", DEFAULT_ROOM_SEAT_COUNT) or DEFAULT_ROOM_SEAT_COUNT)
            bot_count = int(updated_room.get("bot_count", 0) or 0)
            if "seat_count" in patch:
                seat_count = max(int(patch.get("seat_count") or 0), 1)
            if "bot_count" in patch:
                bot_count = max(int(patch.get("bot_count") or 0), 0)
            human_count = len(updated_room["members"])
            if human_count + bot_count > seat_count:
                raise ValueError("座位数不能小于真人和机器人占位总数。")
            updated_room["seat_count"] = seat_count
            updated_room["bot_count"] = bot_count
            updated_room["updated_at"] = time.time()
            normalized_room = self._normalize_room(updated_room, fallback_room_id=str(updated_room.get("room_id", "")))
            if normalized_room is None:
                raise ValueError("房间不存在。")
            self.rooms[normalized_room["room_id"]] = normalized_room
            self._save()
            return self._copy_payload(self._room_snapshot(normalized_room))

    def update_member(self, room_ref: str, client_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        clean_client_id = self._clean_str(client_id)
        if not clean_client_id:
            raise ValueError("用户标识不能为空。")
        if not isinstance(patch, dict):
            raise ValueError("成员更新内容必须是对象。")
        allowed_keys = {"is_ready", "name", "email", "home_city"}
        unknown_keys = sorted(key for key in patch if key not in allowed_keys)
        if unknown_keys:
            raise ValueError(f"成员更新字段不支持: {', '.join(unknown_keys)}")
        with self.lock:
            room = self._require_room(room_ref)
            member_index = self._member_index(room, clean_client_id)
            if member_index is None:
                raise ValueError("房间成员不存在。")
            member = dict(room["members"][member_index])
            if "is_ready" in patch:
                member["is_ready"] = bool(patch.get("is_ready"))
                member["ready"] = bool(patch.get("is_ready"))
            if "name" in patch:
                member["name"] = self._clean_str(patch.get("name"))
            if "email" in patch:
                member["email"] = self._clean_str(patch.get("email")).lower()
            if "home_city" in patch:
                member["home_city"] = self._clean_str(patch.get("home_city"))
            member["updated_at"] = time.time()
            room["members"][member_index] = member
            room["updated_at"] = member["updated_at"]
            normalized_room = self._normalize_room(room, fallback_room_id=str(room.get("room_id", "")))
            if normalized_room is None:
                raise ValueError("房间不存在。")
            self.rooms[normalized_room["room_id"]] = normalized_room
            self._save()
            return self._copy_payload(self._room_snapshot(normalized_room))

    def set_member_ready(self, room_ref: str, client_id: str, is_ready: bool) -> dict[str, Any]:
        return self.update_member(room_ref, client_id, {"is_ready": is_ready})


multiplayer_room_store = MultiplayerRoomStore(MULTIPLAYER_ROOM_STORE_PATH)
