# Copyright (c) 2026 Adam Krúpa and Ondra Csajka. All rights reserved.
import pytest

from identity.principal import IdentityError
from identity.ratelimit import RateLimiter


class Clock:
    now = 0.0

    def __call__(self):
        return self.now


def test_bucket_allows_a_burst_then_refills():
    clock = Clock()
    limiter = RateLimiter(60, burst=3, clock=clock)
    for _ in range(3):
        limiter.check("user:a")
    with pytest.raises(IdentityError) as error:
        limiter.check("user:a")
    assert (error.value.status, error.value.code, error.value.retry_after) == (429, "RATE_LIMITED", 1)
    limiter.check("user:b")  # Other keys are independent.
    clock.now += 1.0
    limiter.check("user:a")


def test_old_full_buckets_are_pruned():
    clock = Clock()
    limiter = RateLimiter(60, clock=clock, max_keys=10)
    for n in range(11):
        limiter.check(f"k{n}")
    clock.now += 3600
    limiter.check("fresh")
    for n in range(10):
        limiter.check(f"x{n}")
    assert len(limiter._buckets) <= 11
