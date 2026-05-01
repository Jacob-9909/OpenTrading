import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from coin_trading.exchange.base import Candle


# Mapping from our interval names to yfinance interval strings.
# Intervals not natively supported are fetched at a finer granularity and resampled.
_YF_INTERVAL: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "10m": "5m",   # fetched as 5m, resampled to 10m
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "1h",    # fetched as 1h, resampled to 4h
    "1d": "1d",
}

# Maximum historical lookback days available per yfinance interval.
_HISTORY_DAYS: dict[str, int] = {
    "1m": 6,
    "5m": 55,
    "15m": 55,
    "30m": 55,
    "1h": 700,
    "1d": 3600,
}

# Pandas resample rule for intervals that require aggregation.
_RESAMPLE_RULE: dict[str, str] = {
    "10m": "10min",
    "4h": "4h",
}

_TIME_DELTA: dict[str, timedelta] = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "10m": timedelta(minutes=10),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


class YFinanceClient:
    """Yahoo Finance market data client for paper stock trading.

    Implements the same interface as BithumbSpotClient / BinanceFuturesClient so it
    can be dropped in without changes to MarketDataCollector or TradingPipeline.
    """

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 200,
        end_time: datetime | None = None,
    ) -> list[Candle]:
        yf_interval = _YF_INTERVAL.get(interval, "1d")
        needs_resample = interval in _RESAMPLE_RULE

        end_dt = end_time or datetime.now(timezone.utc)
        period_days = _HISTORY_DAYS.get(yf_interval, 365)
        start_dt = end_dt - timedelta(days=period_days)

        ticker = yf.Ticker(symbol)
        df: pd.DataFrame = self._fetch_with_retry(
            ticker, yf_interval, start_dt, end_dt
        )

        if df.empty:
            return []

        # Normalize index to UTC DatetimeTZDtype
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize("UTC")
        else:
            df.index = df.index.tz_convert("UTC")

        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        df = df[df["Close"] > 0]

        if needs_resample:
            df = (
                df.resample(_RESAMPLE_RULE[interval])
                .agg(
                    Open=("Open", "first"),
                    High=("High", "max"),
                    Low=("Low", "min"),
                    Close=("Close", "last"),
                    Volume=("Volume", "sum"),
                )
                .dropna(subset=["Open", "Close"])
            )
            df = df[df["Close"] > 0]

        # Exclude rows at or after end_time (yfinance may include the boundary row)
        if end_time:
            end_ts = (
                pd.Timestamp(end_time).tz_localize("UTC")
                if end_time.tzinfo is None
                else pd.Timestamp(end_time).tz_convert("UTC")
            )
            df = df[df.index < end_ts]

        df = df.tail(limit)

        delta = _TIME_DELTA.get(interval, timedelta(hours=1))
        candles: list[Candle] = []
        for ts, row in df.iterrows():
            open_time = ts.to_pydatetime().astimezone(timezone.utc).replace(tzinfo=timezone.utc)
            candles.append(
                Candle(
                    symbol=symbol,
                    timeframe=interval,
                    open_time=open_time,
                    close_time=open_time + delta,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                )
            )
        return sorted(candles, key=lambda c: c.open_time)

    def get_mark_price(self, symbol: str) -> float:
        ticker = yf.Ticker(symbol)
        try:
            price = ticker.fast_info.last_price
            if price is not None and not pd.isna(float(price)):
                return float(price)
        except Exception:
            pass
        # Fallback: last closing price from recent daily history
        df = ticker.history(period="5d", interval="1d", auto_adjust=True)
        if not df.empty and "Close" in df.columns:
            last = df["Close"].dropna()
            if not last.empty:
                return float(last.iloc[-1])
        raise ValueError(f"Cannot retrieve current price for {symbol!r} from Yahoo Finance.")

    # Stub so TradingPipeline can use this client as account_client without errors.
    # With PORTFOLIO_SOURCE=paper this method is never called in practice.
    def get_accounts(self) -> list:
        return []

    @staticmethod
    def _fetch_with_retry(
        ticker: yf.Ticker,
        interval: str,
        start: datetime,
        end: datetime,
        retries: int = 3,
    ) -> pd.DataFrame:
        for attempt in range(retries):
            try:
                return ticker.history(
                    interval=interval,
                    start=start,
                    end=end,
                    auto_adjust=True,
                    prepost=False,
                )
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)
        return pd.DataFrame()
