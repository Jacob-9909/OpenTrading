import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import websockets

_WS_URL = "wss://ws-api.bithumb.com/websocket/v1"
_RECONNECT_DELAY = 5.0
_MIN_CHECK_INTERVAL = 1.0  # seconds between on_price callbacks


def _tf_minutes(tf: str) -> int:
    return {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[tf]


def _floor_to_period(ts: datetime, minutes: int) -> datetime:
    interval = minutes * 60
    return datetime.fromtimestamp((ts.timestamp() // interval) * interval, tz=timezone.utc)


class BithumbTickerMonitor:
    """Public WebSocket ticker for real-time SL/TP price monitoring.

    Subscribes to Bithumb real-time ticker and calls on_price at most once per
    _MIN_CHECK_INTERVAL seconds. Runs in a dedicated daemon thread with
    auto-reconnect on any error.
    """

    def __init__(self, symbol: str, on_price: Callable[[float], None]) -> None:
        self.symbol = symbol
        self.on_price = on_price
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_check = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="bithumb-ws-monitor"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def join(self) -> None:
        if self._thread:
            self._thread.join()

    def _run_loop(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
            except Exception as exc:
                print(f"[WS-ticker] {exc}. Reconnect in {_RECONNECT_DELAY}s...")
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _connect_and_listen(self) -> None:
        async with websockets.connect(_WS_URL, ping_interval=30, ping_timeout=10) as ws:
            print(f"[WS-ticker] Connected → subscribed to {self.symbol} ticker.")
            await ws.send(json.dumps([
                {"ticket": str(uuid.uuid4())},
                {"type": "ticker", "codes": [self.symbol]},
                {"format": "SIMPLE"},
            ]))
            async for raw in ws:
                if self._stop_event.is_set():
                    break
                now = time.monotonic()
                if now - self._last_check < _MIN_CHECK_INTERVAL:
                    continue
                try:
                    data = json.loads(raw)
                    price = float(data["tp"])
                    if price > 0:
                        self._last_check = now
                        self.on_price(price)
                except (KeyError, ValueError, json.JSONDecodeError):
                    pass


@dataclass
class _OpenCandle:
    period_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def update(self, price: float, volume: float) -> None:
        if price > self.high:
            self.high = price
        if price < self.low:
            self.low = price
        self.close = price
        self.volume += volume


class BithumbCandleStreamer:
    """Real-time trade WebSocket → OHLCV candle aggregator → DB upsert.

    Subscribes to Bithumb public trade stream and builds intraday candles
    (10m, 30m, 1h, 4h — 1d excluded) for each requested timeframe.
    Flushes a completed candle to DB when the period boundary is crossed
    and immediately recalculates indicators for that timeframe.

    Historical backfill still requires REST (MarketDataCollector).
    """

    def __init__(
        self,
        symbol: str,
        timeframes: list[str],
        session_factory,
        indicators,
        lookback_limit: int,
    ) -> None:
        self.symbol = symbol
        self._tf_minutes = {tf: _tf_minutes(tf) for tf in timeframes if tf != "1d"}
        self._open: dict[str, _OpenCandle | None] = {tf: None for tf in self._tf_minutes}
        self._session_factory = session_factory
        self._indicators = indicators
        self._lookback_limit = lookback_limit
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="bithumb-ws-candle"
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_listen()
            except Exception as exc:
                print(f"[WS-candle] {exc}. Reconnect in {_RECONNECT_DELAY}s...")
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _connect_and_listen(self) -> None:
        async with websockets.connect(_WS_URL, ping_interval=30, ping_timeout=10) as ws:
            print(f"[WS-candle] Connected → subscribed to {self.symbol} trade.")
            await ws.send(json.dumps([
                {"ticket": str(uuid.uuid4())},
                {"type": "trade", "codes": [self.symbol], "is_only_realtime": True},
                {"format": "SIMPLE"},
            ]))
            async for raw in ws:
                if self._stop_event.is_set():
                    break
                try:
                    data = json.loads(raw)
                    if data.get("ty") != "trade":
                        continue
                    price = float(data["tp"])
                    volume = float(data["tv"])
                    ts = datetime.fromtimestamp(data["ttms"] / 1000, tz=timezone.utc)
                    self._on_trade(price, volume, ts)
                except (KeyError, ValueError, json.JSONDecodeError):
                    pass

    def _on_trade(self, price: float, volume: float, ts: datetime) -> None:
        for tf, minutes in self._tf_minutes.items():
            period_start = _floor_to_period(ts, minutes)
            current = self._open[tf]
            if current is None:
                self._open[tf] = _OpenCandle(period_start, price, price, price, price, volume)
            elif period_start > current.period_start:
                self._flush_candle(tf, current, minutes)
                self._open[tf] = _OpenCandle(period_start, price, price, price, price, volume)
            else:
                current.update(price, volume)

    def _flush_candle(self, timeframe: str, candle: _OpenCandle, minutes: int) -> None:
        from coin_trading.db.models import MarketCandle

        session = self._session_factory()
        try:
            close_time = candle.period_start + timedelta(minutes=minutes)
            existing = (
                session.query(MarketCandle)
                .filter_by(symbol=self.symbol, timeframe=timeframe, open_time=candle.period_start)
                .one_or_none()
            )
            if existing:
                existing.high = max(existing.high, candle.high)
                existing.low = min(existing.low, candle.low)
                existing.close = candle.close
                existing.volume = candle.volume
            else:
                session.add(MarketCandle(
                    symbol=self.symbol,
                    timeframe=timeframe,
                    open_time=candle.period_start,
                    close_time=close_time,
                    open=candle.open,
                    high=candle.high,
                    low=candle.low,
                    close=candle.close,
                    volume=candle.volume,
                ))
            session.commit()
            self._indicators.calculate_latest(
                session, self.symbol, timeframe, self._lookback_limit
            )
        except Exception as exc:
            print(f"[WS-candle] flush {timeframe} ERROR: {exc}")
            session.rollback()
        finally:
            session.close()
