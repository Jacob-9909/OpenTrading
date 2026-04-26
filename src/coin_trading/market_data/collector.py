from datetime import datetime, timezone

from sqlalchemy.orm import Session

from coin_trading.db.models import MarketCandle
from coin_trading.exchange import Candle, MarketDataClient


class MarketDataCollector:
    max_fetch_limit = 200

    def __init__(self, client: MarketDataClient) -> None:
        self.client = client

    def collect_candles(
        self,
        session: Session,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> list[MarketCandle]:
        existing_count = self._candle_count(session, symbol, timeframe)
        if existing_count < limit:
            self._backfill_candles(session, symbol, timeframe, limit)
        else:
            self._collect_incremental(session, symbol, timeframe)
        session.commit()
        return self._recent_candles(session, symbol, timeframe, limit)

    def get_mark_price(self, symbol: str) -> float:
        return self.client.get_mark_price(symbol)

    def _collect_incremental(self, session: Session, symbol: str, timeframe: str) -> None:
        latest = self._latest_candle(session, symbol, timeframe)
        candles = self.client.get_klines(
            symbol=symbol,
            interval=timeframe,
            limit=self.max_fetch_limit,
        )
        for candle in candles:
            if latest is None or self._as_utc(candle.open_time) >= self._as_utc(latest.open_time):
                self._upsert_candle(session, candle)

    def _backfill_candles(
        self,
        session: Session,
        symbol: str,
        timeframe: str,
        target_count: int,
    ) -> None:
        end_time = self._oldest_open_time(session, symbol, timeframe)
        previous_oldest = end_time
        pages = 0
        while self._candle_count(session, symbol, timeframe) < target_count and pages < 20:
            remaining = target_count - self._candle_count(session, symbol, timeframe)
            fetch_limit = min(self.max_fetch_limit, max(remaining, 1))
            candles = self.client.get_klines(
                symbol=symbol,
                interval=timeframe,
                limit=fetch_limit,
                end_time=end_time,
            )
            if not candles:
                break

            new_or_updated = 0
            for candle in candles:
                if previous_oldest is None or candle.open_time < previous_oldest:
                    new_or_updated += 1
                self._upsert_candle(session, candle)
            session.flush()

            oldest = self._oldest_open_time(session, symbol, timeframe)
            if oldest is None or oldest == end_time or new_or_updated == 0:
                break
            previous_oldest = oldest
            end_time = oldest
            pages += 1

        self._collect_incremental(session, symbol, timeframe)

    @staticmethod
    def _candle_count(session: Session, symbol: str, timeframe: str) -> int:
        return session.query(MarketCandle).filter_by(symbol=symbol, timeframe=timeframe).count()

    @staticmethod
    def _latest_candle(session: Session, symbol: str, timeframe: str) -> MarketCandle | None:
        return (
            session.query(MarketCandle)
            .filter_by(symbol=symbol, timeframe=timeframe)
            .order_by(MarketCandle.open_time.desc())
            .first()
        )

    @staticmethod
    def _oldest_open_time(session: Session, symbol: str, timeframe: str) -> datetime | None:
        oldest = (
            session.query(MarketCandle)
            .filter_by(symbol=symbol, timeframe=timeframe)
            .order_by(MarketCandle.open_time.asc())
            .first()
        )
        return MarketDataCollector._as_utc(oldest.open_time) if oldest else None

    @staticmethod
    def _recent_candles(
        session: Session,
        symbol: str,
        timeframe: str,
        limit: int,
    ) -> list[MarketCandle]:
        candles = (
            session.query(MarketCandle)
            .filter_by(symbol=symbol, timeframe=timeframe)
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
            .all()
        )
        return list(reversed(candles))

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _upsert_candle(session: Session, candle: Candle) -> MarketCandle:
        existing = (
            session.query(MarketCandle)
            .filter_by(
                symbol=candle.symbol,
                timeframe=candle.timeframe,
                open_time=candle.open_time,
            )
            .one_or_none()
        )
        if existing:
            existing.close_time = candle.close_time
            existing.open = candle.open
            existing.high = candle.high
            existing.low = candle.low
            existing.close = candle.close
            existing.volume = candle.volume
            return existing

        model = MarketCandle(**candle.model_dump())
        session.add(model)
        return model
