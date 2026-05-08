from datetime import datetime, timezone

import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from coin_trading.db.models import IndicatorSnapshot, MarketCandle


def timeframe_minutes(timeframe: str) -> int:
    """Convert a timeframe string to its duration in minutes."""
    mapping = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "10m": 10,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
        "day": 1440,
        "days": 1440,
    }
    if timeframe not in mapping:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    return mapping[timeframe]


class IndicatorCalculator:
    def calculate_latest(
        self,
        session: Session,
        symbol: str,
        timeframe: str,
        limit: int = 200,
    ) -> IndicatorSnapshot:
        candles = (
            session.query(MarketCandle)
            .filter_by(symbol=symbol, timeframe=timeframe)
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
            .all()
        )
        if len(candles) < 50:
            raise ValueError("At least 50 candles are required for indicator calculation.")

        df = self._to_dataframe(list(reversed(candles)))
        values = self._latest_indicator_values(self.calculate_dataframe(df))
        snapshot = IndicatorSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            calculated_at=datetime.now(timezone.utc),
            values=values,
        )
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)
        return snapshot

    @staticmethod
    def calculate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        close = result["close"]
        high = result["high"]
        low = result["low"]
        volume = result["volume"]

        result["sma_20"] = close.rolling(20).mean()
        result["sma_50"] = close.rolling(50).mean()
        result["ema_12"] = close.ewm(span=12, adjust=False).mean()
        result["ema_26"] = close.ewm(span=26, adjust=False).mean()
        result["macd"] = result["ema_12"] - result["ema_26"]
        result["macd_signal"] = result["macd"].ewm(span=9, adjust=False).mean()
        result["rsi_14"] = IndicatorCalculator._rsi(close, period=14)

        rolling_mean = close.rolling(20).mean()
        rolling_std = close.rolling(20).std()
        result["bb_upper"] = rolling_mean + (rolling_std * 2)
        result["bb_lower"] = rolling_mean - (rolling_std * 2)
        result["bb_percent"] = (close - result["bb_lower"]) / (result["bb_upper"] - result["bb_lower"])

        result["atr_14"] = IndicatorCalculator._atr(high, low, close, period=14)
        result["adx_14"] = IndicatorCalculator._adx(high, low, close, period=14)
        result["volume_sma_20"] = volume.rolling(20).mean()
        result["volume_ratio"] = volume / result["volume_sma_20"]
        ema_gap_pct = (result["ema_12"] - result["ema_26"]) / result["ema_26"] * 100
        result["trend"] = np.where(
            ema_gap_pct >= 0.15, "bullish_strong",
            np.where(ema_gap_pct >= 0.03, "bullish_weak",
            np.where(ema_gap_pct <= -0.15, "bearish_strong",
            np.where(ema_gap_pct <= -0.03, "bearish_weak",
            "neutral")))
        )
        result["trend_strength"] = ema_gap_pct.abs()
        return result

    @staticmethod
    def _to_dataframe(candles: list[MarketCandle]) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": candle.open_time,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
                for candle in candles
            ]
        )

    @staticmethod
    def _latest_indicator_values(df: pd.DataFrame) -> dict[str, object]:
        raw_values = df.iloc[-1].replace({np.nan: None}).to_dict()
        excluded_columns = {"open_time", "open", "high", "low", "close", "volume"}
        return {
            key: IndicatorCalculator._json_value(value)
            for key, value in raw_values.items()
            if key not in excluded_columns
        }

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        previous_close = close.shift(1)
        true_range = pd.concat(
            [(high - low), (high - previous_close).abs(), (low - previous_close).abs()],
            axis=1,
        ).max(axis=1)
        return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    @staticmethod
    def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        atr = IndicatorCalculator._atr(high, low, close, period)
        plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(
            alpha=1 / period, min_periods=period, adjust=False
        ).mean() / atr
        minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(
            alpha=1 / period, min_periods=period, adjust=False
        ).mean() / atr
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    @staticmethod
    def _json_value(value: object) -> object:
        if isinstance(value, (np.floating, float)):
            if np.isnan(value):
                return None
            return float(value)
        if isinstance(value, (np.integer, int)):
            return int(value)
        return value
