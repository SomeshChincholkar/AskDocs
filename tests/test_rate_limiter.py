from app.deps import SlidingWindowRateLimiter


def test_limiter_blocks_at_limit_and_allows_after_window():
    limiter = SlidingWindowRateLimiter()

    assert limiter.allow("client:uploads", limit=2, window_seconds=10, now=100)
    assert limiter.allow("client:uploads", limit=2, window_seconds=10, now=101)
    assert not limiter.allow("client:uploads", limit=2, window_seconds=10, now=102)
    assert limiter.allow("client:uploads", limit=2, window_seconds=10, now=111)


def test_limiter_is_independent_per_action_key():
    limiter = SlidingWindowRateLimiter()

    assert limiter.allow("client:uploads", limit=1, now=100)
    assert limiter.allow("client:questions", limit=1, now=100)
