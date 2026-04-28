from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(slots=True)
class GuardBucket:
    timestamps: deque[float] = field(default_factory=deque)
    blocked_until: float = 0.0


class RequestGuardService:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.buckets: dict[str, GuardBucket] = {}

    def _bucket(self, scope: str, identity: str) -> GuardBucket:
        key = f"{scope}:{identity}"
        bucket = self.buckets.get(key)
        if bucket is None:
            bucket = GuardBucket()
            self.buckets[key] = bucket
        return bucket

    @staticmethod
    def _prune(bucket: GuardBucket, now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        while bucket.timestamps and bucket.timestamps[0] <= cutoff:
            bucket.timestamps.popleft()

    def enforce(
        self,
        *,
        scope: str,
        identity: str,
        limit: int,
        window_seconds: int,
        block_seconds: int = 0,
        message: str = "请求过于频繁，请稍后再试。",
    ) -> None:
        clean_identity = identity.strip().lower()
        if not clean_identity or limit <= 0 or window_seconds <= 0:
            return
        now = time.time()
        with self.lock:
            bucket = self._bucket(scope, clean_identity)
            self._prune(bucket, now, window_seconds)
            if bucket.blocked_until > now:
                wait_seconds = max(1, int(bucket.blocked_until - now))
                raise ValueError(f"{message} 请 {wait_seconds} 秒后重试。")
            if len(bucket.timestamps) >= limit:
                if block_seconds > 0:
                    bucket.blocked_until = now + block_seconds
                    raise ValueError(f"{message} 请 {block_seconds} 秒后重试。")
                raise ValueError(message)
            bucket.timestamps.append(now)

    def reset(self, *, scope: str, identity: str) -> None:
        clean_identity = identity.strip().lower()
        if not clean_identity:
            return
        with self.lock:
            self.buckets.pop(f"{scope}:{clean_identity}", None)


request_guard_service = RequestGuardService()
