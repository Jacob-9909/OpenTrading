from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coin_trading.config import Settings
from coin_trading.db.models import SignalSide, TradeSignal
from coin_trading.db.session import Base
from coin_trading.risk import RiskEngine


class FakeSession:
    def query(self, _model):
        return self

    def filter_by(self, **_kwargs):
        return self

    def filter(self, *_args):
        return self

    def count(self):
        return 0

    def all(self):
        return []

    def add(self, _model):
        return None

    def commit(self):
        return None


class FakeAccountClient:
    def get_accounts(self):
        return [
            {"currency": "KRW", "balance": "700000", "locked": "0"},
            {"currency": "BTC", "balance": "0.01", "locked": "0.002", "avg_buy_price": "90000000"},
        ]


def test_risk_engine_rejects_excessive_leverage() -> None:
    settings = Settings(portfolio_source="paper", max_leverage=3)
    signal = TradeSignal(
        symbol="BTCUSDT",
        side=SignalSide.BUY,
        confidence=0.8,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        leverage=10,
        rationale="too much leverage",
    )

    approval = RiskEngine(settings).evaluate(FakeSession(), signal, mark_price=100)  # type: ignore[arg-type]

    assert approval.approved is False
    assert "exceeds max" in approval.reason


def test_risk_engine_approves_conservative_signal() -> None:
    settings = Settings(
        portfolio_source="paper",
        exchange="binance_futures",
        max_leverage=3,
        liquidation_buffer=0.05,
    )
    signal = TradeSignal(
        symbol="BTCUSDT",
        side=SignalSide.BUY,
        confidence=0.8,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        leverage=2,
        rationale="reasonable leverage",
    )
    signal.allocation_pct = 10

    approval = RiskEngine(settings).evaluate(FakeSession(), signal, mark_price=100)  # type: ignore[arg-type]

    assert approval.approved is True
    assert approval.quantity > 0
    assert approval.liquidation_price == 50


def test_risk_engine_caps_quantity_by_llm_allocation_pct() -> None:
    settings = Settings(
        portfolio_source="paper",
        max_leverage=1,
        max_position_allocation_pct=30,
        initial_equity=1000,
    )
    signal = TradeSignal(
        symbol="KRW-BTC",
        side=SignalSide.BUY,
        confidence=0.8,
        entry_price=100,
        stop_loss=99,
        take_profit=110,
        leverage=1,
        rationale="allocated buy",
    )
    signal.allocation_pct = 10

    approval = RiskEngine(settings).evaluate(FakeSession(), signal, mark_price=100)  # type: ignore[arg-type]

    assert approval.approved is True
    assert approval.quantity == 1


def test_risk_engine_uses_exchange_available_balance_for_spot_sell() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    signal = TradeSignal(
        symbol="KRW-BTC",
        side=SignalSide.SELL,
        confidence=0.8,
        entry_price=100_000_000,
        stop_loss=105_000_000,
        take_profit=95_000_000,
        leverage=1,
        rationale="exit spot position",
    )
    signal.allocation_pct = 100
    session.add(signal)
    session.commit()

    approval = RiskEngine(
        Settings(portfolio_source="exchange", exchange="bithumb_spot"),
        account_client=FakeAccountClient(),
    ).evaluate(session, signal, mark_price=100_000_000)

    assert approval.approved is True
    assert approval.quantity == 0.01
