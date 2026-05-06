from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from coin_trading.config import Settings
from coin_trading.db.models import (
    AppState,
    Position,
    PositionSide,
    PositionStatus,
    RiskEvent,
    RiskEventType,
    SignalSide,
    TradeSignal,
)
from coin_trading.trade.portfolio import PortfolioService


@dataclass(frozen=True)
class RiskApproval:
    approved: bool
    reason: str
    quantity: float = 0
    liquidation_price: float | None = None


class RiskEngine:
    def __init__(self, settings: Settings, account_client: Any | None = None) -> None:
        self.settings = settings
        self.account_client = account_client

    def evaluate(self, session: Session, signal: TradeSignal, mark_price: float) -> RiskApproval:
        if signal.side == SignalSide.HOLD:
            signal.status = "HOLD"
            session.commit()
            return RiskApproval(False, "LLM returned HOLD.")

        if signal.side == SignalSide.CLOSE_POSITION:
            if not signal.close_position_id:
                return RiskApproval(False, "CLOSE_POSITION requires close_position_id.")
            position = session.get(Position, signal.close_position_id)
            if not position or position.status != PositionStatus.OPEN:
                return RiskApproval(False, f"Position {signal.close_position_id} not found or already closed.")
            signal.status = "APPROVED"
            session.commit()
            return RiskApproval(True, "Approved close position.", quantity=position.quantity)

        rejection = self._preflight_rejection(session, signal, mark_price)
        if rejection:
            self._record_rejection(session, signal, rejection)
            return RiskApproval(False, rejection)

        entry = signal.entry_price or mark_price
        stop_loss = signal.stop_loss
        if stop_loss is None:
            reason = "Missing stop loss."
            self._record_rejection(session, signal, reason)
            return RiskApproval(False, reason)

        liquidation_price = None
        if self.settings.exchange == "binance_futures":
            liquidation_price = self.estimate_liquidation_price(
                side=self._position_side(signal.side),
                entry_price=entry,
                leverage=signal.leverage,
            )
            distance = abs(mark_price - liquidation_price) / mark_price
            if distance < self.settings.liquidation_buffer:
                reason = (
                    f"Liquidation buffer too small: {distance:.2%} "
                    f"< {self.settings.liquidation_buffer:.2%}."
                )
                self._record_rejection(
                    session,
                    signal,
                    reason,
                    event_type=RiskEventType.LIQUIDATION_WARNING,
                    payload={"liquidation_price": liquidation_price, "distance": distance},
                )
                return RiskApproval(False, reason, liquidation_price=liquidation_price)

        quantity = self._position_quantity(
            session,
            signal,
            entry_price=entry,
            stop_loss=stop_loss,
            mark_price=mark_price,
        )
        signal.status = "APPROVED"
        session.commit()
        return RiskApproval(True, "Approved by risk engine.", quantity, liquidation_price)

    def monitor_open_positions(self, session: Session, mark_price: float, symbol: str) -> list[RiskEvent]:
        events: list[RiskEvent] = []
        positions = (
            session.query(Position)
            .filter_by(symbol=symbol, status=PositionStatus.OPEN)
            .order_by(Position.opened_at.asc())
            .all()
        )
        for position in positions:
            position.mark_price = mark_price
            position.unrealized_pnl = self._unrealized_pnl(position, mark_price)
            if self.settings.trailing_stop_pct:
                if position.side == PositionSide.LONG:
                    new_stop = round(mark_price * (1 - self.settings.trailing_stop_pct), 8)
                    if position.stop_loss is None or new_stop > position.stop_loss:
                        position.stop_loss = new_stop
                elif position.side == PositionSide.SHORT:
                    new_stop = round(mark_price * (1 + self.settings.trailing_stop_pct), 8)
                    if position.stop_loss is None or new_stop < position.stop_loss:
                        position.stop_loss = new_stop
            if self._hit_stop_loss(position, mark_price):
                events.append(self._event(symbol, RiskEventType.STOP_LOSS, "Stop loss reached.", position))
            elif self._hit_take_profit(position, mark_price):
                events.append(
                    self._event(symbol, RiskEventType.TAKE_PROFIT, "Take profit reached.", position)
                )
            elif position.liquidation_price:
                distance = abs(mark_price - position.liquidation_price) / mark_price
                if distance < self.settings.liquidation_buffer:
                    events.append(
                        self._event(
                            symbol,
                            RiskEventType.LIQUIDATION_WARNING,
                            "Position is near estimated liquidation price.",
                            position,
                        )
                    )
        session.add_all(events)
        session.commit()
        return events

    def estimate_liquidation_price(
        self,
        side: PositionSide,
        entry_price: float,
        leverage: int,
    ) -> float:
        margin_ratio = 1 / max(leverage, 1)
        if side == PositionSide.LONG:
            return entry_price * (1 - margin_ratio)
        return entry_price * (1 + margin_ratio)

    def _preflight_rejection(
        self,
        session: Session,
        signal: TradeSignal,
        mark_price: float,
    ) -> str | None:
        if signal.leverage > self.settings.max_leverage:
            return f"Leverage {signal.leverage} exceeds max {self.settings.max_leverage}."
        if signal.side in {SignalSide.LONG, SignalSide.SHORT} and self._kill_switch_active(session, signal.symbol, mark_price):
            return f"Kill switch: portfolio drawdown exceeds {self.settings.kill_switch_drawdown:.0%}."
        if signal.side in {SignalSide.LONG, SignalSide.SHORT} and self._reentry_cooldown_active(session, signal):
            return f"Re-entry cooldown: {self.settings.reentry_cooldown_minutes}m after last trade."
        if signal.side in {SignalSide.LONG, SignalSide.SHORT} and signal.confidence < 0.50:
            return "Signal confidence is below minimum threshold (0.50)."
        return None

    def _position_quantity(
        self,
        session: Session,
        signal: TradeSignal,
        entry_price: float,
        stop_loss: float,
        mark_price: float,
    ) -> float:
        current_equity = self._current_equity(session, signal.symbol, mark_price)
        risk_budget = current_equity * self.settings.risk_per_trade
        stop_distance = abs(entry_price - stop_loss)
        if stop_distance <= 0:
            return 0
        raw_quantity = risk_budget / stop_distance
        requested_allocation_pct = getattr(signal, "allocation_pct", None)
        allocation_pct = min(
            requested_allocation_pct
            if requested_allocation_pct is not None
            else self.settings.max_position_allocation_pct,
            self.settings.max_position_allocation_pct,
        )
        max_notional = current_equity * (allocation_pct / 100) * self.settings.max_leverage
        return round(min(raw_quantity, max_notional / entry_price), 6)

    def _current_equity(self, session: Session, symbol: str, mark_price: float) -> float:
        snapshot = PortfolioService(self.settings, self.account_client).snapshot(
            session,
            symbol=symbol,
            mark_price=mark_price,
        )
        return snapshot.equity

    def _record_rejection(
        self,
        session: Session,
        signal: TradeSignal,
        reason: str,
        event_type: RiskEventType = RiskEventType.SIGNAL_REJECTED,
        payload: dict | None = None,
    ) -> None:
        signal.status = "REJECTED"
        session.add(
            RiskEvent(
                symbol=signal.symbol,
                event_type=event_type,
                message=reason,
                payload=payload or {"signal_id": signal.id},
            )
        )
        session.commit()

    @staticmethod
    def _unrealized_pnl(position: Position, mark_price: float) -> float:
        if position.side == PositionSide.LONG:
            return (mark_price - position.entry_price) * position.quantity
        return (position.entry_price - mark_price) * position.quantity

    @staticmethod
    def _hit_stop_loss(position: Position, mark_price: float) -> bool:
        if position.stop_loss is None:
            return False
        return (
            position.side == PositionSide.LONG
            and mark_price <= position.stop_loss
            or position.side == PositionSide.SHORT
            and mark_price >= position.stop_loss
        )

    @staticmethod
    def _hit_take_profit(position: Position, mark_price: float) -> bool:
        if position.take_profit is None:
            return False
        return (
            position.side == PositionSide.LONG
            and mark_price >= position.take_profit
            or position.side == PositionSide.SHORT
            and mark_price <= position.take_profit
        )

    @staticmethod
    def _event(
        symbol: str,
        event_type: RiskEventType,
        message: str,
        position: Position,
    ) -> RiskEvent:
        return RiskEvent(
            symbol=symbol,
            event_type=event_type,
            message=message,
            payload={"position_id": position.id, "mark_price": position.mark_price},
        )

    def _kill_switch_active(self, session: Session, symbol: str, mark_price: float) -> bool:
        if self.settings.portfolio_source == "exchange":
            stored = AppState.get(session, f"baseline_equity:{symbol}")
            baseline = float(stored) if stored else None
        else:
            baseline = self.settings.initial_equity
        if not baseline or baseline <= 0:
            return False
        current = self._current_equity(session, symbol, mark_price)
        return (current - baseline) / baseline <= -self.settings.kill_switch_drawdown

    def _reentry_cooldown_active(self, session: Session, signal: TradeSignal) -> bool:
        if self.settings.reentry_cooldown_minutes <= 0 or signal.side not in {SignalSide.LONG, SignalSide.SHORT}:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.settings.reentry_cooldown_minutes)
        recent_trade = (
            session.query(TradeSignal)
            .filter(
                TradeSignal.symbol == signal.symbol,
                TradeSignal.side.in_([SignalSide.LONG, SignalSide.SHORT]),
                TradeSignal.created_at >= cutoff,
                TradeSignal.status.notin_(["PENDING", "REJECTED"]),
            )
            .first()
        )
        return recent_trade is not None

    def _position_side(self, signal_side: SignalSide) -> PositionSide:
        if signal_side == SignalSide.SHORT:
            return PositionSide.SHORT
        return PositionSide.LONG
