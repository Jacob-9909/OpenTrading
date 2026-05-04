from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from coin_trading.db.models import PaperOrder, Position, TradeSignal
from coin_trading.trade.risk import RiskApproval


class BaseExecutor(ABC):
    @abstractmethod
    def execute(
        self,
        session: Session,
        signal: TradeSignal,
        approval: RiskApproval,
        mark_price: float,
    ) -> PaperOrder | None: ...

    @abstractmethod
    def emergency_exit(
        self,
        session: Session,
        position: Position,
        mark_price: float,
        reason: str,
    ) -> PaperOrder: ...
