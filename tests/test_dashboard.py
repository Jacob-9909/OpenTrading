from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd

from coin_trading.dashboard import _chart_candle_limit, _hold_signals_in_range, _orders_in_range
from coin_trading.market.indicators import timeframe_minutes as _timeframe_minutes


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


def test_hold_signals_in_range_maps_price_to_last_close_before_signal() -> None:
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=30)
    t1 = now - timedelta(minutes=10)
    candle_df = pd.DataFrame({"time": [t0, t1], "open": [0, 0], "high": [0, 0], "low": [0, 0], "close": [100.0, 110.0]})
    sig = SimpleNamespace(created_at=now, entry_price=None)

    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sig]

    df = _hold_signals_in_range(session, "BTC-KRW", candle_df, t0, now + timedelta(minutes=1))

    assert len(df) == 1
    assert df.iloc[0]["price"] == 110.0


def test_hold_signals_in_range_uses_entry_price_when_set() -> None:
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=30)
    candle_df = pd.DataFrame({"time": [t0], "open": [0], "high": [0], "low": [0], "close": [100.0]})
    sig = SimpleNamespace(created_at=now, entry_price=99.5)

    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sig]

    df = _hold_signals_in_range(session, "BTC-KRW", candle_df, t0, now + timedelta(minutes=1))

    assert len(df) == 1
    assert df.iloc[0]["price"] == 99.5


def test_hold_signals_in_range_skips_signals_before_first_candle() -> None:
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(minutes=10)
    candle_df = pd.DataFrame({"time": [t0], "open": [0], "high": [0], "low": [0], "close": [100.0]})
    sig = SimpleNamespace(created_at=now - timedelta(hours=1), entry_price=None)

    session = MagicMock()
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = [sig]

    df = _hold_signals_in_range(session, "BTC-KRW", candle_df, now - timedelta(hours=2), now)

    assert df.empty
