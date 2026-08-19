from afuture.risk import OrderRateLimiter


def test_order_rate_limiter_blocks_runaway_submissions():
    limiter = OrderRateLimiter(max_orders_per_minute=3)
    assert limiter.allow(0.0)
    assert limiter.allow(1.0)
    assert limiter.allow(2.0)
    assert not limiter.allow(3.0)
    assert limiter.allow(61.0)
