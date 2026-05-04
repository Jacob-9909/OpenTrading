from datetime import datetime
from typing import Protocol

from pydantic import BaseModel


class Candle(BaseModel):
    symbol: str
    timeframe: str
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketDataClient(Protocol):
    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        raise NotImplementedError

    def get_mark_price(self, symbol: str) -> float:
        raise NotImplementedError
