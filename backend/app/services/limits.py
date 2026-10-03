"""Concurrency and rate limits for the pipeline (Phase 7).

Bursts must create a backlog, not overload. Two distinct mechanisms:

* **Concurrency limiters** -- a semaphore per stage (fetch, AI, embedding) so
  one expensive stage cannot starve the others, and so the number of
  simultaneous outbound calls stays bounded no matter how many jobs arrive.

* **Rate limiters** -- a token bucket per provider key, so we stay inside an
  external API's published quota and absorb 429s by slowing down *before* the
  provider has to reject us.

Both are configured from the environment and default to conservative values.
Neither adds any new queueing technology: the limiter only decides when a
worker may start the next call, and the work itself is still plain Celery.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(1, value)


# Per-stage concurrency. Separate knobs on purpose: embedding is a batched,
# token-heavy call and must not be allowed to crowd out fetching.
FETCH_CONCURRENCY = _env_int("FETCH_CONCURRENCY", 4)
AI_CONCURRENCY = _env_int("AI_CONCURRENCY", 4)
EMBEDDING_CONCURRENCY = _env_int("EMBEDDING_CONCURRENCY", 2)

# External API quota: sustained calls per second and the burst we allow.
AI_RATE_PER_SEC = float(os.getenv("AI_RATE_PER_SEC", "5") or 5)
AI_BURST = _env_int("AI_BURST", 10)


class ConcurrencyLimiter:
    """A counting semaphore that records how busy it got.

    Used as a context manager. `peak` is what lets a test assert that the limit
    was genuinely respected rather than merely configured.
    """

    def __init__(self, name: str, limit: int):
        self.name = name
        self.limit = limit
        self._semaphore = threading.BoundedSemaphore(limit)
        self._lock = threading.Lock()
        self.current = 0
        self.peak = 0
        self.total = 0

    def __enter__(self) -> "ConcurrencyLimiter":
        self._semaphore.acquire()
        with self._lock:
            self.current += 1
            self.total += 1
            self.peak = max(self.peak, self.current)
        return self

    def __exit__(self, *exc_info) -> None:
        with self._lock:
            self.current -= 1
        self._semaphore.release()

    def reset_stats(self) -> None:
        with self._lock:
            self.current = 0
            self.peak = 0
            self.total = 0


FETCH_LIMIT = ConcurrencyLimiter("fetch", FETCH_CONCURRENCY)
AI_LIMIT = ConcurrencyLimiter("ai", AI_CONCURRENCY)
EMBEDDING_LIMIT = ConcurrencyLimiter("embedding", EMBEDDING_CONCURRENCY)
LIMITERS = (FETCH_LIMIT, AI_LIMIT, EMBEDDING_LIMIT)


class RateLimiter:
    """Token bucket over a sliding window, one bucket per provider key.

    Deliberately a sliding window rather than a fixed one: a fixed window lets
    twice the quota through across a boundary, which is exactly the burst that
    provokes a 429.
    """

    def __init__(self, rate_per_sec: float, burst: int):
        self.rate_per_sec = max(0.01, float(rate_per_sec))
        self.burst = max(1, int(burst))
        self._lock = threading.Lock()
        self._calls = deque()
        self.peak_in_window = 0

    def _trim_locked(self, now: float) -> None:
        window = 1.0
        while self._calls and now - self._calls[0] >= window:
            self._calls.popleft()

    def try_acquire(self) -> bool:
        """Take a slot if one is free right now. False means 'come back later'."""
        now = time.monotonic()
        with self._lock:
            self._trim_locked(now)
            if len(self._calls) >= self.burst:
                self.peak_in_window = max(self.peak_in_window, len(self._calls))
                return False
            self._calls.append(now)
            self.peak_in_window = max(self.peak_in_window, len(self._calls))
            return True

    def acquire(self, sleep=time.sleep) -> bool:
        """Block until a slot is free. Used from synchronous call sites."""
        while not self.try_acquire():
            sleep(min(0.05, 1.0 / self.rate_per_sec))

    def reset_stats(self) -> None:
        with self._lock:
            self._calls.clear()
            self.peak_in_window = 0


_AI_RATE = RateLimiter(AI_RATE_PER_SEC, AI_BURST)
_RATE_LIMITERS = {"default": _AI_RATE}


def rate_limiter_for(provider: str) -> RateLimiter:
    """The bucket for a provider. Unknown providers share the default quota."""
    return _RATE_LIMITERS.setdefault(provider, _AI_RATE)


def reset_all_stats() -> None:
    """Test helper: forget observed peaks without changing the limits."""
    for limiter in LIMITERS:
        limiter.reset_stats()
    for rate in _RATE_LIMITERS.values():
        rate.reset_stats()