from datetime import datetime, timezone
from enum import StrEnum

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy import JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.orm import Session

from coin_trading.db.session import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SignalSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class RiskEventType(StrEnum):
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    LIQUIDATION_WARNING = "LIQUIDATION_WARNING"
    DAILY_LOSS_LIMIT = "DAILY_LOSS_LIMIT"
    KILL_SWITCH = "KILL_SWITCH"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"


class MarketCandle(Base):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("symbol", "timeframe", "open_time", name="uq_market_candle_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class IndicatorSnapshot(Base):
    __tablename__ = "indicator_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    timeframe: Mapped[str] = mapped_column(String(16), index=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    values: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class NewsItem(Base):
    __tablename__ = "news_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(String(1000), unique=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LLMDecision(Base):
    __tablename__ = "llm_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    prompt_summary: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSON)
    token_usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    signals: Mapped[list["TradeSignal"]] = relationship(back_populates="llm_decision")


class TradeSignal(Base):
    __tablename__ = "trade_signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    llm_decision_id: Mapped[int | None] = mapped_column(ForeignKey("llm_decisions.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[SignalSide] = mapped_column(SAEnum(SignalSide))
    confidence: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[int] = mapped_column(Integer, default=1)
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="PENDING")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    llm_decision: Mapped[LLMDecision | None] = relationship(back_populates="signals")
    orders: Mapped[list["PaperOrder"]] = relationship(back_populates="signal")


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_signal_id: Mapped[int | None] = mapped_column(ForeignKey("trade_signals.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[OrderSide] = mapped_column(SAEnum(OrderSide))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    signal: Mapped[TradeSignal | None] = relationship(back_populates="orders")


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[PositionSide] = mapped_column(SAEnum(PositionSide))
    status: Mapped[PositionStatus] = mapped_column(SAEnum(PositionStatus), default=PositionStatus.OPEN)
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    mark_price: Mapped[float] = mapped_column(Float)
    liquidation_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[int] = mapped_column(Integer, default=1)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    event_type: Mapped[RiskEventType] = mapped_column(SAEnum(RiskEventType))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AppState(Base):
    """Key-value store for persistent application state (e.g. baseline_equity)."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    @classmethod
    def get(cls, session: Session, key: str) -> str | None:
        row = session.get(cls, key)
        return row.value if row else None

    @classmethod
    def set(cls, session: Session, key: str, value: str) -> None:
        row = session.get(cls, key)
        if row is None:
            session.add(cls(key=key, value=value))
        else:
            row.value = value
            row.updated_at = utc_now()
        session.commit()
