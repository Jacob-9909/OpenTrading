from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coin_trading.db.models import MarketCandle
from coin_trading.db.session import Base
from coin_trading.exchange import Candle
from coin_trading.market_data import MarketDataCollector


class FakeMarketDataClient:
    def __init__(self, candles: list[Candle]) -> None:
        self.candles = candles
        self.calls: list[datetime | None] = []

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        self.calls.append(end_time)
        candidates = [
            candle
            for candle in self.candles
            if candle.symbol == symbol
            and candle.timeframe == interval
            and (end_time is None or candle.open_time < end_time)
        ]
        return candidates[-limit:]

    def get_mark_price(self, _symbol: str) -> float:
        return 100.0


def test_collector_backfills_when_db_is_empty() -> None:
    session = _session()
    candles = _candles(10)
    collector = MarketDataCollector(FakeMarketDataClient(candles))

    stored = collector.collect_candles(session, "KRW-BTC", "1h", limit=10)

    assert len(stored) == 10
    assert session.query(MarketCandle).count() == 10


def test_collector_only_adds_new_candles_after_initial_history() -> None:
    session = _session()
    candles = _candles(12)
    collector = MarketDataCollector(FakeMarketDataClient(candles[:10]))
    collector.collect_candles(session, "KRW-BTC", "1h", limit=10)

    collector.client.candles = candles  # type: ignore[attr-defined]
    stored = collector.collect_candles(session, "KRW-BTC", "1h", limit=10)

    assert len(stored) == 10
    assert session.query(MarketCandle).count() == 12
    assert stored[-1].open_time.replace(tzinfo=timezone.utc) == candles[-1].open_time


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _candles(count: int) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [
        Candle(
            symbol="KRW-BTC",
            timeframe="1h",
            open_time=start + timedelta(hours=index),
            close_time=start + timedelta(hours=index + 1),
            open=100 + index,
            high=102 + index,
            low=99 + index,
            close=101 + index,
            volume=1 + index,
        )
        for index in range(count)
    ]
