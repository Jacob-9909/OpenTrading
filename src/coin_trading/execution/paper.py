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
from coin_trading.risk import RiskApproval


class PaperExecutor:
    def execute(
        self,
        session: Session,
        signal: TradeSignal,
        approval: RiskApproval,
        mark_price: float,
    ) -> PaperOrder:
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

        order_side = self._order_side(signal.side)
        entry_price = signal.entry_price or mark_price
        order = PaperOrder(
            trade_signal_id=signal.id,
            symbol=signal.symbol,
            side=order_side,
            quantity=approval.quantity,
            price=entry_price,
            status=OrderStatus.FILLED,
        )
        if signal.side == SignalSide.SELL:
            self._close_spot_positions(session, signal.symbol, entry_price)
            signal.status = "EXECUTED"
            session.add(order)
            session.commit()
            session.refresh(order)
            return order

        if signal.side == SignalSide.BUY:
            position_side = PositionSide.SPOT if "-" in signal.symbol else PositionSide.LONG
        else:
            position_side = PositionSide.SHORT
        position = Position(
            symbol=signal.symbol,
            side=position_side,
            quantity=approval.quantity,
            entry_price=entry_price,
            mark_price=mark_price,
            liquidation_price=approval.liquidation_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            leverage=signal.leverage,
        )
        signal.status = "EXECUTED"
        session.add_all([order, position])
        session.commit()
        session.refresh(order)
        return order

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
