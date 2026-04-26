from datetime import datetime, timezone
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from coin_trading.exchange.base import Candle


class BinanceFuturesClient:
    def __init__(self, base_url: str = "https://fapi.binance.com", timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=5))
    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_time:
            params["endTime"] = int(end_time.timestamp() * 1000)
        response = httpx.get(
            f"{self.base_url}/fapi/v1/klines",
            params=params,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [self._parse_kline(symbol, interval, row) for row in response.json()]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=5))
    def get_mark_price(self, symbol: str) -> float:
        response = httpx.get(
            f"{self.base_url}/fapi/v1/premiumIndex",
            params={"symbol": symbol},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return float(response.json()["markPrice"])

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, min=0.5, max=5))
    def get_open_interest(self, symbol: str) -> float | None:
        response = httpx.get(
            f"{self.base_url}/fapi/v1/openInterest",
            params={"symbol": symbol},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return float(payload["openInterest"]) if "openInterest" in payload else None

    @staticmethod
    def _parse_kline(symbol: str, timeframe: str, row: list[Any]) -> Candle:
        return Candle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
            close_time=datetime.fromtimestamp(row[6] / 1000, tz=timezone.utc),
        )
