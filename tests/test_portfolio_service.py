from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coin_trading.config import Settings
from coin_trading.db.models import Position, PositionSide
from coin_trading.db.session import Base
from coin_trading.trade import PortfolioService


class FakeAccountClient:
    def get_accounts(self):
        return [
            {"currency": "KRW", "balance": "700000", "locked": "50000"},
            {"currency": "BTC", "balance": "0.01", "locked": "0.002", "avg_buy_price": "90000000"},
        ]


def test_exchange_snapshot_uses_bithumb_account_balances() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    snapshot = PortfolioService(
        Settings(portfolio_source="exchange", initial_equity=1_000_000),
        account_client=FakeAccountClient(),
    ).snapshot(session, symbol="KRW-BTC", mark_price=100_000_000)

    assert snapshot.source == "exchange"
    assert snapshot.cash_available == 700_000
    assert snapshot.open_position_value == 1_200_000
    assert snapshot.equity == 1_950_000
    assert snapshot.unrealized_pnl == 120_000
    assert snapshot.avg_entry_price == 90_000_000
    assert round(snapshot.position_return_pct, 2) == 11.11


def test_paper_snapshot_includes_avg_entry_and_position_return() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(
        Position(
            symbol="KRW-BTC",
            side=PositionSide.SPOT,
            quantity=0.01,
            entry_price=90_000_000,
            mark_price=100_000_000,
            leverage=1,
        )
    )
    session.commit()

    snapshot = PortfolioService(
        Settings(portfolio_source="paper", initial_equity=1_000_000),
    ).snapshot(session)

    assert snapshot.avg_entry_price == 90_000_000
    assert round(snapshot.position_return_pct, 2) == 11.11
    assert snapshot.open_position_value == 1_000_000
