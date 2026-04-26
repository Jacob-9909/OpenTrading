from datetime import datetime, timedelta, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coin_trading.config import Settings
from coin_trading.db.models import (
    MarketCandle,
    OrderSide,
    OrderStatus,
    PaperOrder,
    SignalSide,
    TradeSignal,
)
from coin_trading.db.session import Base
from coin_trading.risk import RiskApproval
from coin_trading.scheduler.jobs import TradingPipeline


class FakeMarketData:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def collect_candles(self, session, symbol: str, timeframe: str, limit: int):
        self.calls.append(f"{symbol}:{timeframe}:{limit}")
        now = datetime.now(timezone.utc)
        candle = MarketCandle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=now - timedelta(hours=1),
            close_time=now,
            open=90,
            high=110,
            low=80,
            close=100,
            volume=10,
        )
        session.add(candle)
        session.commit()
        return [candle]

    def get_mark_price(self, _symbol: str) -> float:
        return 100.0


class RaisingMarketData:
    def collect_candles(self, *_args, **_kwargs):
        raise AssertionError("decide_once must not collect candles")

    def get_mark_price(self, *_args, **_kwargs):
        raise AssertionError("decide_once must not fetch mark price")


class FakeIndicators:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def calculate_latest(self, _session, symbol: str, timeframe: str, limit: int):
        self.calls.append(f"{symbol}:{timeframe}:{limit}")


class FakeNews:
    def __init__(self) -> None:
        self.calls = 0

    def collect(self, _session) -> None:
        self.calls += 1


class FakeRisk:
    def monitor_open_positions(self, *_args, **_kwargs):
        return []

    def evaluate(self, session, signal, _latest_price):
        signal.status = "APPROVED"
        session.commit()
        return RiskApproval(True, "Approved.", quantity=1)


class FakeStrategy:
    def create_signal(self, session, symbol: str, timeframe: str, latest_price: float):
        signal = TradeSignal(
            symbol=symbol,
            side=SignalSide.HOLD,
            confidence=0.8,
            entry_price=latest_price,
            stop_loss=latest_price * 0.95,
            take_profit=latest_price * 1.1,
            rationale="test decision",
        )
        session.add(signal)
        session.commit()
        session.refresh(signal)
        return signal


class FakeExecutor:
    def execute(self, session, signal: TradeSignal, _approval: RiskApproval, latest_price: float):
        order = PaperOrder(
            trade_signal_id=signal.id,
            symbol=signal.symbol,
            side=OrderSide.BUY,
            quantity=1,
            price=latest_price,
            status=OrderStatus.FILLED,
        )
        session.add(order)
        session.commit()
        session.refresh(order)
        return order


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _pipeline(settings: Settings) -> TradingPipeline:
    pipeline = object.__new__(TradingPipeline)
    pipeline.settings = settings
    pipeline.market_data = FakeMarketData()
    pipeline.indicators = FakeIndicators()
    pipeline.news = FakeNews()
    pipeline.risk = FakeRisk()
    pipeline.strategy = FakeStrategy()
    pipeline.executor = FakeExecutor()
    return pipeline


def _add_fresh_candle(session, symbol: str = "KRW-BTC", timeframe: str = "1h") -> None:
    now = datetime.now(timezone.utc)
    session.add(
        MarketCandle(
            symbol=symbol,
            timeframe=timeframe,
            open_time=now - timedelta(hours=1),
            close_time=now,
            open=90,
            high=110,
            low=80,
            close=100,
            volume=10,
        )
    )
    session.commit()


def test_refresh_data_once_updates_data_without_creating_signal() -> None:
    session = _session()
    settings = Settings(
        symbol="KRW-BTC",
        timeframe="1h",
        analysis_timeframes=["4h"],
        lookback_limit=200,
        dashboard_chart_timeframe="10m",
        dashboard_chart_days=10,
    )
    pipeline = _pipeline(settings)

    result = pipeline.refresh_data_once(session)

    assert result.latest_price == 100
    assert result.refreshed_timeframes == ["1h", "4h", "1d", "10m"]
    assert session.query(TradeSignal).count() == 0
    assert pipeline.news.calls == 1
    assert pipeline.indicators.calls == [
        "KRW-BTC:1h:200",
        "KRW-BTC:4h:200",
        "KRW-BTC:1d:200",
    ]
    assert pipeline.market_data.calls == [
        "KRW-BTC:1h:200",
        "KRW-BTC:4h:200",
        "KRW-BTC:1d:200",
        "KRW-BTC:10m:1440",
    ]


def test_decide_once_uses_existing_db_data_without_refreshing() -> None:
    session = _session()
    _add_fresh_candle(session)
    settings = Settings(symbol="KRW-BTC", timeframe="1h", trading_mode="paper")
    pipeline = _pipeline(settings)
    pipeline.market_data = RaisingMarketData()

    result = pipeline.decide_once(session)

    assert result.signal_id is not None
    assert result.signal_status == "APPROVED"
    assert result.order_id is not None
    assert session.query(TradeSignal).count() == 1


def test_decide_once_blocks_stale_market_data() -> None:
    session = _session()
    old = datetime.now(timezone.utc) - timedelta(days=1)
    session.add(
        MarketCandle(
            symbol="KRW-BTC",
            timeframe="1h",
            open_time=old - timedelta(hours=1),
            close_time=old,
            open=90,
            high=110,
            low=80,
            close=100,
            volume=10,
        )
    )
    session.commit()
    pipeline = _pipeline(Settings(max_data_staleness_minutes=180))

    result = pipeline.decide_once(session)

    assert result.signal_status == "STALE_DATA"
    assert result.order_id is None
    assert session.query(TradeSignal).count() == 0


def test_decide_once_blocks_duplicate_daily_decision() -> None:
    session = _session()
    _add_fresh_candle(session)
    session.add(
        TradeSignal(
            symbol="KRW-BTC",
            side=SignalSide.HOLD,
            confidence=0.8,
            rationale="already decided",
        )
    )
    session.commit()
    pipeline = _pipeline(Settings(scheduler_timezone="Asia/Seoul", decision_cooldown_minutes=1440))

    result = pipeline.decide_once(session)

    assert result.signal_status == "SKIPPED"
    assert result.order_id is None
    assert session.query(TradeSignal).count() == 1


def test_decide_once_allows_duplicate_when_cooldown_is_disabled() -> None:
    session = _session()
    _add_fresh_candle(session)
    session.add(
        TradeSignal(
            symbol="KRW-BTC",
            side=SignalSide.HOLD,
            confidence=0.8,
            rationale="previous decision",
        )
    )
    session.commit()
    pipeline = _pipeline(Settings(decision_cooldown_minutes=0))

    result = pipeline.decide_once(session)

    assert result.signal_id is not None
    assert result.signal_status == "APPROVED"
    assert session.query(TradeSignal).count() == 2


def test_run_once_refreshes_before_decision() -> None:
    session = _session()
    pipeline = _pipeline(
        Settings(
            symbol="KRW-BTC",
            timeframe="1h",
            lookback_limit=200,
            analysis_timeframes=[],
            decision_cooldown_minutes=0,
            dashboard_chart_timeframe="10m",
            dashboard_chart_days=10,
        )
    )

    result = pipeline.run_once(session)

    assert result.signal_id is not None
    assert result.signal_status == "APPROVED"
    assert pipeline.market_data.calls == [
        "KRW-BTC:1h:200",
        "KRW-BTC:1d:200",
        "KRW-BTC:10m:1440",
    ]
    assert pipeline.news.calls == 1


def test_refresh_data_once_uses_larger_limit_when_dashboard_timeframe_matches_trading_timeframe() -> None:
    session = _session()
    pipeline = _pipeline(
        Settings(
            symbol="KRW-BTC",
            timeframe="1h",
            lookback_limit=200,
            analysis_timeframes=[],
            dashboard_chart_timeframe="1h",
            dashboard_chart_days=10,
        )
    )

    result = pipeline.refresh_data_once(session)

    assert result.refreshed_timeframes == ["1h", "1d"]
    assert pipeline.market_data.calls == ["KRW-BTC:1h:240", "KRW-BTC:1d:200"]
