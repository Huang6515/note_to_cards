"""进程内滑动窗口限流。

给"公开部署的演示站"提供一个零依赖的保护层，避免接口变成任何人都能刷的
LLM 代理。**单进程内存实现**：多 worker / 多副本部署时请改用 Redis，
或在反向代理层（Nginx / Caddy）做更严格的限流。
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock


class SlidingWindowLimiter:
    """按 key（通常是客户端 IP）统计窗口内的请求数。

    ``limit <= 0`` 表示关闭限流。
    """

    def __init__(self, limit: int, window_seconds: float = 60.0, max_keys: int = 10000) -> None:
        self.limit = max(0, int(limit))
        self.window_seconds = max(1.0, float(window_seconds))
        self.max_keys = max(1, int(max_keys))
        self._hits: dict[str, deque] = {}
        self._lock = Lock()

    @property
    def enabled(self) -> bool:
        return self.limit > 0

    def _drop_expired(self, now: float) -> None:
        """清掉所有已过期的记录，防止内存无限增长。"""
        cutoff = now - self.window_seconds
        for key in list(self._hits):
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                del self._hits[key]

    def _enforce_capacity(self, now: float) -> None:
        """为新 key 腾位置：先清过期记录，再按最早命中时间淘汰。

        只在 key 不存在时调用，避免把"当前这个 key"自己淘汰掉（那会重置它的
        计数窗口，反而绕过限流）。
        """
        if len(self._hits) < self.max_keys:
            return
        self._drop_expired(now)
        while len(self._hits) >= self.max_keys:
            oldest = min(self._hits, key=lambda k: self._hits[k][0])
            del self._hits[oldest]

    def allow(self, key: str) -> bool:
        """记录一次请求；返回 False 表示已超限（且不记录）。"""
        if not self.enabled:
            return True
        now = time.monotonic()
        with self._lock:
            if key not in self._hits:
                self._enforce_capacity(now)
            hits = self._hits.setdefault(key, deque())
            cutoff = now - self.window_seconds
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def retry_after(self, key: str) -> float:
        """还需要等多少秒才允许下一次请求（未超限返回 0）。"""
        if not self.enabled:
            return 0.0
        with self._lock:
            hits = self._hits.get(key)
            if not hits or len(hits) < self.limit:
                return 0.0
            return max(0.0, hits[0] + self.window_seconds - time.monotonic())

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
