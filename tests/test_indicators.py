from datetime import datetime, timedelta, timezone

import pandas as pd

from coin_trading.market import IndicatorCalculator


def test_indicator_calculator_adds_core_columns() -> None:
    now = datetime.now(timezone.utc)
    df = pd.DataFrame(
        [
            {
                "open_time": now + timedelta(hours=i),
                "open": 100 + i,
                "high": 102 + i,
                "low": 98 + i,
                "close": 101 + i,
                "volume": 10 + i,
            }
            for i in range(80)
        ]
    )

    result = IndicatorCalculator.calculate_dataframe(df)

    assert {"rsi_14", "macd", "bb_upper", "atr_14", "adx_14", "trend"}.issubset(result.columns)
    assert result.iloc[-1]["trend"] == "bullish"


def test_latest_indicator_values_are_json_safe() -> None:
    now = datetime.now(timezone.utc)
    df = pd.DataFrame(
        [
            {
                "open_time": now + timedelta(hours=i),
                "open": 100 + i,
                "high": 102 + i,
                "low": 98 + i,
                "close": 101 + i,
                "volume": 10 + i,
            }
            for i in range(80)
        ]
    )

    values = IndicatorCalculator._latest_indicator_values(IndicatorCalculator.calculate_dataframe(df))

    assert "open_time" not in values
    assert "close" not in values
    assert isinstance(values["rsi_14"], float)
