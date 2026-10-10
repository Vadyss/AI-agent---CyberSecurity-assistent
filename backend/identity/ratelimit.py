# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
"""In-process token buckets (one backend instance per deployment, see D-012)."""
from __future__ import annotations

import math
import time

from .principal import IdentityError


class RateLimiter:
    def __init__(self, per_minute: int, *, burst: int | None = None, clock=time.monotonic,
                 max_keys: int = 100_000) -> None:
        if per_minute < 1:
            raise ValueError("The rate must be positive.")
        self.rate = per_minute / 60.0
        self.capacity = float(burst or per_minute)
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}

    def check(self, key: str) -> None:
        """Take one token for ``key`` or raise 429 with the wait time."""
        now = self.clock()
        tokens, updated = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - updated) * self.rate)
        if tokens < 1:
            self._buckets[key] = (tokens, now)
            retry_after = max(1, math.ceil((1 - tokens) / self.rate))
            error = IdentityError(429, "RATE_LIMITED", f"Too many requests; retry in {retry_after} s.")
            error.retry_after = retry_after
            raise error
        self._buckets[key] = (tokens - 1, now)
        if len(self._buckets) > self.max_keys:
            self._prune(now)

    def _prune(self, now: float) -> None:
        # Full buckets carry no state worth keeping.
        full_after = self.capacity / self.rate
        self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < full_after}
