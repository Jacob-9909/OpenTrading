import json
from typing import Any

from sqlalchemy.orm import Session

from coin_trading.config import Settings
from coin_trading.db.models import (
    OrderSide,
    OrderStatus,
    PaperOrder,
    Position,
    PositionSide,
    PositionStatus,
    SignalSide,
    TradeSignal,
    utc_now,
)
from coin_trading.exchange.bithumb import BithumbSpotClient
from coin_trading.risk import RiskApproval


class BithumbLiveExecutor:
    def __init__(self, settings: Settings, client: BithumbSpotClient) -> None:
        self.settings = settings
        self.client = client

    def execute(
        self,
        session: Session,
        signal: TradeSignal,
        approval: RiskApproval,
        mark_price: float,
    ) -> PaperOrder:
        if not approval.approved or approval.quantity <= 0:
            return self._reject(session, signal, approval.reason, mark_price)

        safety_rejection = self._safety_rejection(signal, approval, mark_price)
        if safety_rejection:
            return self._reject(session, signal, safety_rejection, mark_price)

        side = self._order_side(signal.side)
        price = signal.entry_price or mark_price
        response = self._place_order(signal, approval.quantity, price)
        order = PaperOrder(
            trade_signal_id=signal.id,
            symbol=signal.symbol,
            side=side,
            quantity=approval.quantity,
            price=price,
            status=OrderStatus.SUBMITTED,
            reason=json.dumps(response, ensure_ascii=False),
        )
        signal.status = "SUBMITTED"
        session.add(order)

        # Shadow position tracking for P&L reporting (mirrors paper executor logic)
        if signal.side == SignalSide.BUY:
            position = Position(
                symbol=signal.symbol,
                side=PositionSide.SPOT,
                quantity=approval.quantity,
                entry_price=price,
                mark_price=mark_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                leverage=1,
            )
            session.add(position)
        elif signal.side == SignalSide.SELL:
            self._close_spot_positions(session, signal.symbol, price)

        session.commit()
        session.refresh(order)
        return order

    def _safety_rejection(
        self,
        signal: TradeSignal,
        approval: RiskApproval,
        mark_price: float,
    ) -> str | None:
        if self.settings.exchange != "bithumb_spot":
            return "Live executor supports only Bithumb spot trading."
        if not self.settings.live_trading_enabled:
            return "Live trading is disabled. Set LIVE_TRADING_ENABLED=true to place real orders."
        if signal.side not in {SignalSide.BUY, SignalSide.SELL}:
            return f"Unsupported live trading signal side: {signal.side}."

        notional = approval.quantity * (signal.entry_price or mark_price)
        if notional < self.settings.live_min_order_krw:
            return (
                f"Order notional {notional:.0f} KRW is below live minimum "
                f"{self.settings.live_min_order_krw:.0f} KRW."
            )
        if notional > self.settings.live_max_order_krw:
            return (
                f"Order notional {notional:.0f} KRW exceeds live maximum "
                f"{self.settings.live_max_order_krw:.0f} KRW."
            )
        return None

    def _place_order(self, signal: TradeSignal, quantity: float, price: float) -> dict[str, Any]:
        if self.settings.live_order_type == "market":
            if signal.side == SignalSide.BUY:
                return self.client.place_market_buy(signal.symbol, quote_amount=quantity * price)
            return self.client.place_market_sell(signal.symbol, volume=quantity)

        side = "bid" if signal.side == SignalSide.BUY else "ask"
        return self.client.place_limit_order(signal.symbol, side=side, volume=quantity, price=price)

    @staticmethod
    def _order_side(signal_side: SignalSide) -> OrderSide:
        return OrderSide.BUY if signal_side == SignalSide.BUY else OrderSide.SELL

    @staticmethod
    def _close_spot_positions(session: Session, symbol: str, exit_price: float) -> None:
        positions = (
            session.query(Position)
            .filter_by(symbol=symbol, status=PositionStatus.OPEN)
            .all()
        )
        for position in positions:
            position.mark_price = exit_price
            position.realized_pnl = (exit_price - position.entry_price) * position.quantity
            position.unrealized_pnl = 0
            position.status = PositionStatus.CLOSED
            position.closed_at = utc_now()

    def _reject(
        self,
        session: Session,
        signal: TradeSignal,
        reason: str,
        mark_price: float,
    ) -> PaperOrder:
        order = PaperOrder(
            trade_signal_id=signal.id,
            symbol=signal.symbol,
            side=self._order_side(signal.side),
            quantity=0,
            price=signal.entry_price or mark_price,
            status=OrderStatus.REJECTED,
            reason=reason,
        )
        signal.status = "REJECTED"
        session.add(order)
        session.commit()
        session.refresh(order)
        return order
