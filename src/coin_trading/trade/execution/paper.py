from sqlalchemy.orm import Session

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
from coin_trading.trade import RiskApproval
from coin_trading.trade.execution.base import BaseExecutor


class PaperExecutor(BaseExecutor):
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
            order = PaperOrder(
                trade_signal_id=signal.id,
                symbol=signal.symbol,
                side=self._order_side(signal.side),
                quantity=0,
                price=mark_price,
                status=OrderStatus.REJECTED,
                reason=approval.reason,
            )
            session.add(order)
            session.commit()
            return order

        if signal.side == SignalSide.CLOSE_POSITION:
            position = session.get(Position, signal.close_position_id)
            if not position or position.status != PositionStatus.OPEN:
                raise ValueError(f"Position {signal.close_position_id} not open.")
            
            self._close_position_by_id(session, position.id, mark_price)
            order_side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY
            order = PaperOrder(
                trade_signal_id=signal.id,
                symbol=signal.symbol,
                side=order_side,
                quantity=approval.quantity,
                price=mark_price,
                status=OrderStatus.FILLED,
            )
            signal.status = "EXECUTED"
            session.add(order)
            session.commit()
            session.refresh(order)
            return order

        order_side = self._order_side(signal.side)
        order = PaperOrder(
            trade_signal_id=signal.id,
            symbol=signal.symbol,
            side=order_side,
            quantity=approval.quantity,
            price=mark_price,
            status=OrderStatus.FILLED,
        )

        position_side = PositionSide.LONG if signal.side == SignalSide.LONG else PositionSide.SHORT
        leverage = signal.leverage if signal.leverage else 1
        if position_side == PositionSide.LONG:
            liquidation_price = approval.liquidation_price or round(mark_price * (1 - 1 / max(leverage, 1)), 8)
        else:
            liquidation_price = approval.liquidation_price or round(mark_price * (1 + 1 / max(leverage, 1)), 8)

        # Adjust SL/TP to preserve LLM's intended offset from the actual entry price.
        # LLM calculates SL/TP relative to signal.entry_price; actual fill is mark_price.
        llm_entry = signal.entry_price or mark_price
        if signal.stop_loss is not None and llm_entry != mark_price:
            sl_offset = signal.stop_loss - llm_entry
            actual_stop_loss = round(mark_price + sl_offset, 8)
        else:
            actual_stop_loss = signal.stop_loss

        if signal.take_profit is not None and llm_entry != mark_price:
            tp_offset = signal.take_profit - llm_entry
            actual_take_profit = round(mark_price + tp_offset, 8)
        else:
            actual_take_profit = signal.take_profit

        position = Position(
            symbol=signal.symbol,
            side=position_side,
            quantity=approval.quantity,
            entry_price=mark_price,
            mark_price=mark_price,
            liquidation_price=liquidation_price,
            stop_loss=actual_stop_loss,
            take_profit=actual_take_profit,
            leverage=leverage,
        )
        signal.status = "EXECUTED"
        session.add_all([order, position])
        session.commit()
        session.refresh(order)
        return order

    def emergency_exit(
        self,
        session: Session,
        position: Position,
        mark_price: float,
        reason: str,
    ) -> PaperOrder:
        self._close_positions(session, position.symbol, position.side, mark_price)
        order_side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY
        order = PaperOrder(
            symbol=position.symbol,
            side=order_side,
            quantity=position.quantity,
            price=mark_price,
            status=OrderStatus.FILLED,
            reason=reason,
        )
        session.add(order)
        session.commit()
        session.refresh(order)
        return order

    @staticmethod
    def _order_side(signal_side: SignalSide) -> OrderSide:
        return OrderSide.BUY if signal_side == SignalSide.LONG else OrderSide.SELL

    @staticmethod
    def _close_position_by_id(session: Session, position_id: int, exit_price: float) -> None:
        position = session.get(Position, position_id)
        if position and position.status == PositionStatus.OPEN:
            if position.side == PositionSide.LONG:
                position.realized_pnl = (exit_price - position.entry_price) * position.quantity
            else:
                position.realized_pnl = (position.entry_price - exit_price) * position.quantity
            position.mark_price = exit_price
            position.unrealized_pnl = 0
            position.status = PositionStatus.CLOSED
            position.closed_at = utc_now()

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
