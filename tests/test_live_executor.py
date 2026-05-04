from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coin_trading.config import Settings
from coin_trading.db.models import OrderStatus, SignalSide, TradeSignal
from coin_trading.db.session import Base
from coin_trading.trade.execution.live_bithumb import BithumbLiveExecutor
from coin_trading.trade import RiskApproval


class FakeBithumbClient:
    def __init__(self) -> None:
        self.orders: list[tuple[str, dict]] = []

    def place_limit_order(self, market: str, side: str, volume: float, price: float):
        body = {"market": market, "side": side, "volume": volume, "price": price}
        self.orders.append(("limit", body))
        return {"uuid": "order-1", **body}

    def place_market_buy(self, market: str, quote_amount: float):
        body = {"market": market, "quote_amount": quote_amount}
        self.orders.append(("market_buy", body))
        return {"uuid": "order-1", **body}

    def place_market_sell(self, market: str, volume: float):
        body = {"market": market, "volume": volume}
        self.orders.append(("market_sell", body))
        return {"uuid": "order-1", **body}


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_live_executor_blocks_when_safety_flag_is_disabled() -> None:
    session = _session()
    signal = TradeSignal(
        symbol="KRW-BTC",
        side=SignalSide.BUY,
        confidence=0.8,
        entry_price=100_000_000,
        stop_loss=95_000_000,
        take_profit=110_000_000,
        rationale="valid setup",
    )
    session.add(signal)
    session.commit()
    client = FakeBithumbClient()

    order = BithumbLiveExecutor(
        Settings(trading_mode="live", live_trading_enabled=False),
        client,  # type: ignore[arg-type]
    ).execute(session, signal, RiskApproval(True, "Approved.", quantity=0.001), mark_price=100_000_000)

    assert order.status == OrderStatus.REJECTED
    assert client.orders == []


def test_live_executor_places_limit_buy_when_enabled() -> None:
    session = _session()
    signal = TradeSignal(
        symbol="KRW-BTC",
        side=SignalSide.BUY,
        confidence=0.8,
        entry_price=100_000_000,
        stop_loss=95_000_000,
        take_profit=110_000_000,
        rationale="valid setup",
    )
    session.add(signal)
    session.commit()
    client = FakeBithumbClient()

    order = BithumbLiveExecutor(
        Settings(
            trading_mode="live",
            live_trading_enabled=True,
            live_max_order_krw=200_000,
        ),
        client,  # type: ignore[arg-type]
    ).execute(session, signal, RiskApproval(True, "Approved.", quantity=0.001), mark_price=100_000_000)

    assert order.status == OrderStatus.SUBMITTED
    assert signal.status == "SUBMITTED"
    assert client.orders == [
        (
            "limit",
            {"market": "KRW-BTC", "side": "bid", "volume": 0.001, "price": 100_000_000},
        )
    ]


def test_live_executor_rejects_order_above_live_max_notional() -> None:
    session = _session()
    signal = TradeSignal(
        symbol="KRW-BTC",
        side=SignalSide.BUY,
        confidence=0.8,
        entry_price=100_000_000,
        stop_loss=95_000_000,
        take_profit=110_000_000,
        rationale="valid setup",
    )
    session.add(signal)
    session.commit()
    client = FakeBithumbClient()

    order = BithumbLiveExecutor(
        Settings(
            trading_mode="live",
            live_trading_enabled=True,
            live_max_order_krw=50_000,
        ),
        client,  # type: ignore[arg-type]
    ).execute(session, signal, RiskApproval(True, "Approved.", quantity=0.001), mark_price=100_000_000)

    assert order.status == OrderStatus.REJECTED
    assert "exceeds live maximum" in str(order.reason)
    assert client.orders == []
