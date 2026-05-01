from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.orm import Session

from coin_trading.config import Settings
from coin_trading.db.models import AppState, Position, PositionSide, PositionStatus


@dataclass(frozen=True)
class PortfolioSnapshot:
    source: str
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    return_pct: float
    open_positions: int
    open_position_value: float
    cash_available: float
    avg_entry_price: float = 0
    position_return_pct: float = 0
    base_asset_quantity: float = 0
    quote_locked: float = 0
    base_locked: float = 0


class AccountClient(Protocol):
    def get_accounts(self) -> list[dict[str, Any]]:
        raise NotImplementedError


class PortfolioService:
    def __init__(self, settings: Settings, account_client: AccountClient | None = None) -> None:
        self.settings = settings
        self.account_client = account_client

    def snapshot(
        self,
        session: Session,
        symbol: str | None = None,
        mark_price: float | None = None,
    ) -> PortfolioSnapshot:
        if self.settings.portfolio_source == "exchange":
            if not symbol or mark_price is None:
                raise ValueError("symbol and mark_price are required for exchange portfolio snapshots.")
            return self._exchange_snapshot(session=session, symbol=symbol, mark_price=mark_price)
        return self._paper_snapshot(session)

    def _paper_snapshot(self, session: Session) -> PortfolioSnapshot:
        positions = session.query(Position).all()
        realized = sum(position.realized_pnl for position in positions)
        unrealized = sum(
            self._unrealized(position)
            for position in positions
            if position.status == PositionStatus.OPEN
        )
        open_position_value = sum(
            position.mark_price * position.quantity
            for position in positions
            if position.status == PositionStatus.OPEN
        )
        open_positions = [
            position for position in positions if position.status == PositionStatus.OPEN
        ]
        open_cost_basis = sum(position.entry_price * position.quantity for position in open_positions)
        avg_entry_price = (
            open_cost_basis / sum(position.quantity for position in open_positions)
            if open_positions
            else 0
        )
        initial = self.settings.initial_equity or 0
        equity = initial + realized + unrealized
        return PortfolioSnapshot(
            source="paper",
            equity=equity,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            return_pct=(equity / initial - 1) * 100 if initial else 0,
            open_positions=len(open_positions),
            open_position_value=open_position_value,
            cash_available=max(equity - open_position_value, 0),
            avg_entry_price=avg_entry_price,
            position_return_pct=(
                (open_position_value / open_cost_basis - 1) * 100 if open_cost_basis > 0 else 0
            ),
        )

    def _exchange_snapshot(self, session: Session, symbol: str, mark_price: float) -> PortfolioSnapshot:
        if self.account_client is None:
            raise RuntimeError("Exchange portfolio source requires an authenticated account client.")

        accounts = self.account_client.get_accounts()
        quote_currency, base_currency = self._split_symbol(symbol)
        quote = self._account_for(accounts, quote_currency)
        base = self._account_for(accounts, base_currency)

        quote_balance = self._float(quote.get("balance"))
        quote_locked = self._float(quote.get("locked"))
        base_balance = self._float(base.get("balance"))
        base_locked = self._float(base.get("locked"))
        base_total = base_balance + base_locked
        quote_total = quote_balance + quote_locked
        open_position_value = base_total * mark_price
        equity = quote_total + open_position_value

        # ── 기준 자산 자동 기록 (첫 실행 시 계좌 잔고를 baseline으로 저장) ─────
        baseline_key = f"baseline_equity:{symbol}"
        stored = AppState.get(session, baseline_key)
        if stored is None:
            AppState.set(session, baseline_key, str(equity))
            baseline_equity = equity
        else:
            baseline_equity = float(stored)

        avg_buy_price = self._float(base.get("avg_buy_price"))
        unrealized = (mark_price - avg_buy_price) * base_total if avg_buy_price > 0 else 0
        position_return_pct = (
            (mark_price / avg_buy_price - 1) * 100 if avg_buy_price > 0 and base_total > 0 else 0
        )

        # Accumulate realized P&L from shadow positions created by the live executor
        realized_pnl = sum(
            pos.realized_pnl
            for pos in session.query(Position).filter_by(symbol=symbol, status=PositionStatus.CLOSED).all()
        )

        return PortfolioSnapshot(
            source="exchange",
            equity=equity,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized,
            return_pct=(equity / baseline_equity - 1) * 100 if baseline_equity else 0,
            open_positions=1 if base_total > 0 else 0,
            open_position_value=open_position_value,
            cash_available=quote_balance,
            avg_entry_price=avg_buy_price,
            position_return_pct=position_return_pct,
            base_asset_quantity=base_total,
            quote_locked=quote_locked,
            base_locked=base_locked,
        )

    @staticmethod
    def _unrealized(position: Position) -> float:
        if position.side in {PositionSide.LONG, PositionSide.SPOT}:
            return (position.mark_price - position.entry_price) * position.quantity
        return (position.entry_price - position.mark_price) * position.quantity

    @staticmethod
    def _split_symbol(symbol: str) -> tuple[str, str]:
        if "-" not in symbol:
            raise ValueError(f"Expected spot symbol like KRW-BTC, got {symbol}.")
        quote_currency, base_currency = symbol.split("-", maxsplit=1)
        return quote_currency, base_currency

    @staticmethod
    def _account_for(accounts: list[dict[str, Any]], currency: str) -> dict[str, Any]:
        for account in accounts:
            if str(account.get("currency", "")).upper() == currency.upper():
                return account
        return {"currency": currency, "balance": "0", "locked": "0", "avg_buy_price": "0"}

    @staticmethod
    def _float(value: Any) -> float:
        if value in (None, ""):
            return 0
        return float(value)
