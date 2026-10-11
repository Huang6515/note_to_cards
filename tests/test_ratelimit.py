"""滑动窗口限流器单元测试。"""

from ratelimit import SlidingWindowLimiter


def test_allows_up_to_limit():
    limiter = SlidingWindowLimiter(2, 60.0)
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False


def test_keys_are_independent():
    limiter = SlidingWindowLimiter(1, 60.0)
    assert limiter.allow("a") is True
    assert limiter.allow("b") is True
    assert limiter.allow("a") is False


def test_disabled_when_limit_is_zero():
    limiter = SlidingWindowLimiter(0, 60.0)
    assert limiter.enabled is False
    assert all(limiter.allow("ip") for _ in range(100))


def test_retry_after_only_for_throttled_keys():
    limiter = SlidingWindowLimiter(1, 60.0)
    assert limiter.allow("ip") is True
    assert limiter.retry_after("ip") > 0
    assert limiter.retry_after("another") == 0.0


def test_window_expires_old_hits(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr("ratelimit.time.monotonic", lambda: clock["now"])
    limiter = SlidingWindowLimiter(1, 10.0)

    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False
    clock["now"] += 11
    assert limiter.allow("ip") is True


def test_reset_clears_state():
    limiter = SlidingWindowLimiter(1, 60.0)
    limiter.allow("ip")
    assert limiter.allow("ip") is False
    limiter.reset()
    assert limiter.allow("ip") is True


def test_memory_stays_bounded():
    limiter = SlidingWindowLimiter(5, 60.0, max_keys=10)
    for i in range(200):
        limiter.allow(f"ip-{i}")
    assert len(limiter._hits) <= 10
