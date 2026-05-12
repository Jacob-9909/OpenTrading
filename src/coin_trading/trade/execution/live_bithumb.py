import json
from typing import Any

import httpx
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
from coin_trading.market.exchange.bithumb import BithumbSpotClient
from coin_trading.trade import RiskApproval
from coin_trading.trade.execution.base import BaseExecutor


class BithumbLiveExecutor(BaseExecutor):
    def __init__(self, settings: Settings, client: BithumbSpotClient) -> None:
        self.settings = settings
        self.client = client

    def execute(
        self,
        session: Session,
        signal: TradeSignal,
        approval: RiskApproval,
        mark_price: float,
    ) -> PaperOrder | None:
        if signal.side == SignalSide.HOLD:
            return None
        if not approval.approved or approval.quantity <= 0:
            return self._reject(session, signal, approval.reason, mark_price)

        safety_rejection = self._safety_rejection(signal, approval, mark_price)
        if safety_rejection:
            return self._reject(session, signal, safety_rejection, mark_price)

        side = self._order_side(signal.side)
        if self.settings.live_order_type == "market":
            order_price = mark_price
        else:
            order_price = signal.entry_price or mark_price
        response = self._place_order(signal, approval.quantity, order_price)
        order = PaperOrder(
            trade_signal_id=signal.id,
            symbol=signal.symbol,
            side=side,
            quantity=approval.quantity,
            price=mark_price,
            status=OrderStatus.SUBMITTED,
            reason=json.dumps(response, ensure_ascii=False),
        )
        signal.status = "SUBMITTED"
        session.add(order)

        # Shadow position tracking for P&L reporting (mirrors paper executor logic)
        if signal.side == SignalSide.LONG:
            self._close_positions(session, signal.symbol, PositionSide.SHORT, mark_price)
            existing = (
                session.query(Position)
                .filter_by(symbol=signal.symbol, side=PositionSide.LONG, status=PositionStatus.OPEN)
                .order_by(Position.opened_at.asc())
                .first()
            )
            if existing is not None:
                old_q = existing.quantity
                new_q = approval.quantity
                total_q = round(old_q + new_q, 6)
                existing.entry_price = round(
                    (existing.entry_price * old_q + mark_price * new_q) / total_q,
                    8,
                )
                existing.quantity = total_q
                existing.mark_price = mark_price
                existing.stop_loss = signal.stop_loss
                existing.take_profit = signal.take_profit
                session.add(existing)
            else:
                position = Position(
                    symbol=signal.symbol,
                    side=PositionSide.LONG,
                    quantity=approval.quantity,
                    entry_price=mark_price,
                    mark_price=mark_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    leverage=1,
                )
                session.add(position)
        elif signal.side == SignalSide.SHORT:
            self._close_positions(session, signal.symbol, PositionSide.LONG, mark_price)

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
        if signal.side not in {SignalSide.LONG, SignalSide.SHORT}:
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
            if signal.side == SignalSide.LONG:
                return self.client.place_market_buy(signal.symbol, quote_amount=quantity * price)
            return self.client.place_market_sell(signal.symbol, volume=quantity)

        side = "bid" if signal.side == SignalSide.LONG else "ask"
        return self.client.place_limit_order(signal.symbol, side=side, volume=quantity, price=price)

    @staticmethod
    def _order_side(signal_side: SignalSide) -> OrderSide:
        return OrderSide.BUY if signal_side == SignalSide.LONG else OrderSide.SELL

    @staticmethod
    def _close_positions(session: Session, symbol: str, side: PositionSide, exit_price: float) -> None:
        positions = (
            session.query(Position)
            .filter_by(symbol=symbol, side=side, status=PositionStatus.OPEN)
            .all()
        )
        for position in positions:
            if position.side == PositionSide.LONG:
                position.realized_pnl = (exit_price - position.entry_price) * position.quantity
            else:
                position.realized_pnl = (position.entry_price - exit_price) * position.quantity
            position.mark_price = exit_price
            position.unrealized_pnl = 0
            position.status = PositionStatus.CLOSED
            position.closed_at = utc_now()

    def emergency_exit(
        self,
        session: Session,
        position: Position,
        mark_price: float,
        reason: str,
    ) -> PaperOrder:
        response: dict = {"simulated": True}
        if self.settings.live_trading_enabled:
            try:
                response = self.client.place_market_sell(position.symbol, volume=position.quantity)
            except httpx.HTTPError as exc:
                response = {"error": str(exc)}
        self._close_positions(session, position.symbol, position.side, mark_price)
        order = PaperOrder(
            symbol=position.symbol,
            side=OrderSide.SELL,
            quantity=position.quantity,
            price=mark_price,
            status=OrderStatus.FILLED,
            reason=json.dumps({"exit_reason": reason, "response": response}, ensure_ascii=False),
        )
        session.add(order)
        session.commit()
        session.refresh(order)
        return order

    def reconcile_submitted_orders(self, session: Session, symbol: str) -> None:
        submitted = (
            session.query(PaperOrder)
            .filter_by(symbol=symbol, status=OrderStatus.SUBMITTED)
            .all()
        )
        for order in submitted:
            try:
                response = json.loads(order.reason or "{}")
                order_uuid = response.get("uuid")
                if not order_uuid:
                    continue
                status_response = self.client.get_order(order_uuid)
                bithumb_state = status_response.get("state")
                if bithumb_state == "done":
                    order.status = OrderStatus.FILLED
                elif bithumb_state == "cancel":
                    order.status = OrderStatus.REJECTED
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError):
                pass
        session.commit()

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
