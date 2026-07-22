"""Unit tests for the sliding-window RateLimiter (login / ask throttling)."""

from services.rate_limit import RateLimiter


def test_allows_up_to_limit():
    rl = RateLimiter(max_events=3, window_seconds=60)
    assert rl.hit("a") is True
    assert rl.hit("a") is True
    assert rl.hit("a") is True
    # 4th within window is blocked
    assert rl.hit("a") is False


def test_keys_are_independent():
    rl = RateLimiter(max_events=1, window_seconds=60)
    assert rl.hit("a") is True
    assert rl.hit("b") is True
    assert rl.hit("a") is False


def test_window_expiry_allows_again(monkeypatch):
    import services.rate_limit as mod

    t = [1000.0]
    monkeypatch.setattr(mod.time, "time", lambda: t[0])

    rl = RateLimiter(max_events=1, window_seconds=10)
    assert rl.hit("a") is True
    assert rl.hit("a") is False
    # advance past the window
    t[0] += 11
    assert rl.hit("a") is True
