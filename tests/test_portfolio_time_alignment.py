from datetime import datetime, timedelta, timezone

from afuture.portfolio_risk import PortfolioRiskAnalyzer


def test_timestamped_correlation_uses_common_time_buckets_only():
    analyzer = PortfolioRiskAnalyzer(window=10, min_samples=4, bucket_seconds=60)
    base = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
    for index, value in enumerate([1, 2, 4, 3, 6, 5]):
        analyzer.update("left", value, base + timedelta(minutes=index))
        # Same shape but ten minutes later: index alignment would be a false +1 correlation.
        analyzer.update("right", value * 2, base + timedelta(minutes=index + 10))
    assert analyzer.correlation("left", "right") == 0.0


def test_timestamped_correlation_detects_aligned_common_buckets():
    analyzer = PortfolioRiskAnalyzer(window=10, min_samples=4, bucket_seconds=60)
    base = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
    for index, value in enumerate([1, 2, 4, 3, 6, 5]):
        timestamp = base + timedelta(minutes=index)
        analyzer.update("left", value, timestamp)
        analyzer.update("right", value * 2, timestamp + timedelta(seconds=15))
    assert analyzer.correlation("left", "right") > 0.99
