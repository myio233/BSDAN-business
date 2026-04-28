from __future__ import annotations

import json

import pytest

from exschool_game.multiplayer_store import MultiplayerRoomStore


def test_multiplayer_store_create_join_and_snapshot_placeholders(tmp_path) -> None:
    store = MultiplayerRoomStore(tmp_path / "multiplayer_rooms.json")
    host = {"client_id": "host-1", "name": "Host", "email": "HOST@example.com"}
    guest = {"client_id": "guest-1", "name": "Guest", "email": "guest@example.com"}

    created = store.create_room(
        host,
        room_name="Alpha Room",
        seat_count=5,
        bot_count=1,
        metadata={"mode": "multiplayer"},
    )
    joined = store.join_room(created["room_code"].lower(), guest)
    ready = store.set_member_ready(created["room_id"], "host-1", True)
    ready = store.set_member_ready(created["room_id"], "guest-1", True)

    assert created["room_name"] == "Alpha Room"
    assert created["host_client_id"] == "host-1"
    assert created["human_count"] == 1
    assert created["bot_count"] == 1
    assert created["open_seat_count"] == 3
    assert len(created["room_code"]) == 6

    assert joined["human_count"] == 2
    assert joined["open_seat_count"] == 2
    assert ready["ready_count"] == 2
    assert ready["all_ready"] is True
    assert ready["metadata"] == {"mode": "multiplayer"}
    assert [seat["slot_type"] for seat in ready["seats"]].count("human") == 2
    assert [seat["slot_type"] for seat in ready["seats"]].count("bot") == 1
    assert [seat["slot_type"] for seat in ready["seats"]].count("open") == 2

    created["members"][0]["name"] = "Mutated"
    reloaded = store.get_room(created["room_id"])
    assert reloaded is not None
    assert reloaded["members"][0]["name"] == "Host"
    assert reloaded["members"][0]["email"] == "host@example.com"


def test_multiplayer_store_leave_reassigns_host_and_deletes_empty_room(tmp_path) -> None:
    store = MultiplayerRoomStore(tmp_path / "multiplayer_rooms.json")
    room = store.create_room({"client_id": "host-1", "name": "Host", "email": "host@example.com"}, seat_count=4)
    store.join_room(room["room_id"], {"client_id": "guest-1", "name": "Guest 1", "email": "g1@example.com"})
    store.join_room(room["room_id"], {"client_id": "guest-2", "name": "Guest 2", "email": "g2@example.com"})

    after_host_leave = store.leave_room(room["room_id"], "host-1")
    after_second_leave = store.leave_room(room["room_code"], "guest-1")
    after_last_leave = store.leave_room(room["room_id"], "guest-2")

    assert after_host_leave is not None
    assert after_host_leave["host_client_id"] == "guest-1"
    assert after_second_leave is not None
    assert after_second_leave["host_client_id"] == "guest-2"
    assert after_last_leave is None
    assert store.get_room(room["room_id"]) is None


def test_multiplayer_store_update_room_enforces_capacity_and_supports_code_lookup(tmp_path) -> None:
    store = MultiplayerRoomStore(tmp_path / "multiplayer_rooms.json")
    room = store.create_room({"client_id": "host-1", "name": "Host", "email": "host@example.com"}, seat_count=4, bot_count=1)
    store.join_room(room["room_id"], {"client_id": "guest-1", "name": "Guest", "email": "guest@example.com"})

    updated = store.update_room(
        room["room_code"],
        {
            "seat_count": 6,
            "bot_count": 2,
            "status": "ready-check",
            "metadata": {"round": "lobby"},
            "room_name": "Expanded Room",
            "host_client_id": "guest-1",
        },
    )

    assert updated["room_name"] == "Expanded Room"
    assert updated["seat_count"] == 6
    assert updated["bot_count"] == 2
    assert updated["status"] == "ready-check"
    assert updated["metadata"] == {"round": "lobby"}
    assert updated["host_client_id"] == "guest-1"
    assert store.get_room(room["room_code"]) is not None

    with pytest.raises(ValueError, match="座位数不能小于真人和机器人占位总数"):
        store.update_room(room["room_id"], {"seat_count": 2, "bot_count": 2})

    with pytest.raises(ValueError, match="房主必须是房间成员"):
        store.update_room(room["room_id"], {"host_client_id": "missing-user"})


def test_multiplayer_store_recovers_from_backup_and_clears_load_error(tmp_path) -> None:
    path = tmp_path / "multiplayer_rooms.json"
    backup_path = path.with_suffix(".json.bak")
    path.write_text("{broken", encoding="utf-8")
    backup_path.write_text(
        json.dumps(
            {
                "rooms": {
                    "room-1": {
                        "room_id": "room-1",
                        "room_code": "ROOM42",
                        "room_name": "Recovered Room",
                        "host_client_id": "host-1",
                        "seat_count": 4,
                        "bot_count": 1,
                        "status": "waiting",
                        "metadata": {},
                        "created_at": 1.0,
                        "updated_at": 1.0,
                        "members": [
                            {
                                "client_id": "host-1",
                                "name": "Recovered",
                                "email": "recovered@example.com",
                                "is_ready": False,
                                "joined_at": 1.0,
                                "updated_at": 1.0,
                                "seat_index": 0,
                            }
                        ],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    store = MultiplayerRoomStore(path)

    recovered = store.get_room("ROOM42")
    assert store.load_error is None
    assert recovered is not None
    assert recovered["room_name"] == "Recovered Room"
