from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coin_trading.config import Settings
from coin_trading.db.models import IndicatorSnapshot, MarketCandle
from coin_trading.db.session import Base
from coin_trading.agent.context import LLMContextBuilder


class FakeAccountClient:
    def get_accounts(self):
        return [
            {"currency": "KRW", "balance": "700000", "locked": "50000"},
            {"currency": "BTC", "balance": "0.01", "locked": "0.002", "avg_buy_price": "200"},
        ]


def test_context_builder_includes_expanded_market_evidence() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    now = datetime.now(timezone.utc)

    for index in range(90):
        close = 100 + index
        session.add(
            MarketCandle(
                symbol="KRW-BTC",
                timeframe="1d",
                open_time=now - timedelta(days=89 - index),
                close_time=now - timedelta(days=88 - index),
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                volume=10 + index,
            )
        )

    for index in range(80):
        close = 180 + index
        session.add(
            MarketCandle(
                symbol="KRW-BTC",
                timeframe="1h",
                open_time=now - timedelta(hours=79 - index),
                close_time=now - timedelta(hours=78 - index),
                open=close - 1,
                high=close + 2,
                low=close - 2,
                close=close,
                volume=5 + index,
            )
        )

    for timeframe in ["1h", "4h", "1d"]:
        session.add(
            IndicatorSnapshot(
                symbol="KRW-BTC",
                timeframe=timeframe,
                calculated_at=now,
                values={"rsi_14": 55.0, "trend": "bullish"},
            )
        )
    session.commit()

    context = LLMContextBuilder(
        settings=Settings(
            portfolio_source="paper",
            initial_equity=1_000_000,
            max_position_allocation_pct=25,
        ),
        analysis_timeframes=["1h", "4h", "1d"],
        recent_candle_limit=60,
    ).build(session, "KRW-BTC", "1h", latest_price=259)

    assert len(context["recent_candles"]) == 60
    assert context["monthly_summary"]["days"] == 30
    assert context["quarter_summary"]["days"] == 90
    assert context["portfolio"]["current_equity"] == 1_000_000
    assert context["portfolio"]["max_position_allocation_pct"] == 25
    assert set(context["multi_timeframe"]) == {"1h", "4h", "1d"}
    assert context["multi_timeframe"]["1d"]["indicators"]["trend"] == "bullish"


def test_context_builder_can_use_bithumb_exchange_portfolio() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    context = LLMContextBuilder(
        settings=Settings(
            portfolio_source="exchange",
            initial_equity=1_000_000,
            max_position_allocation_pct=30,
        ),
        account_client=FakeAccountClient(),
    ).build(session, "KRW-BTC", "1h", latest_price=100_000_000)

    assert context["portfolio"]["source"] == "exchange"
    assert context["portfolio"]["cash_available"] == 700_000
    assert context["portfolio"]["base_asset_quantity"] == 0.012
    assert context["portfolio"]["current_equity"] == 1_950_000
