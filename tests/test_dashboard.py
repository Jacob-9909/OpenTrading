from datetime import datetime, timedelta, timezone

from coin_trading.dashboard import _chart_candle_limit, _orders_in_range, _timeframe_minutes


class FakeSide:
    value = "BUY"


class FakeOrder:
    def __init__(self, created_at: datetime) -> None:
        self.created_at = created_at
        self.price = 100
        self.side = FakeSide()


def test_dashboard_chart_limit_defaults_to_ten_days_of_10m_candles() -> None:
    assert _chart_candle_limit(10, "10m") == 1440


def test_dashboard_chart_limit_caps_very_dense_ranges() -> None:
    assert _chart_candle_limit(10, "1m") == 4000


def test_dashboard_timeframe_minutes_supports_minute_timeframes() -> None:
    assert _timeframe_minutes("10m") == 10
    assert _timeframe_minutes("1h") == 60


def test_orders_in_range_filters_markers_to_chart_window() -> None:
    now = datetime.now(timezone.utc)
    orders = [
        FakeOrder(now - timedelta(days=2)),
        FakeOrder(now),
        FakeOrder(now + timedelta(days=2)),
    ]

    df = _orders_in_range(orders, now - timedelta(hours=1), now + timedelta(hours=1))

    assert len(df) == 1
    assert df.iloc[0]["price"] == 100
