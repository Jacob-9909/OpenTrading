import pytest
from pydantic import ValidationError

from coin_trading.agent.schemas import TradingDecision


def test_long_decision_requires_stop_below_entry_and_target_above() -> None:
    decision = TradingDecision(
        action="LONG",
        confidence=0.8,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        allocation_pct=20,
        leverage=2,
        rationale="valid setup",
    )

    assert decision.action == "LONG"


def test_short_decision_requires_take_profit_below_entry_and_stop_above() -> None:
    decision = TradingDecision(
        action="SHORT",
        confidence=0.8,
        entry_price=100,
        stop_loss=110,
        take_profit=90,
        allocation_pct=20,
        leverage=2,
        rationale="valid short setup",
    )

    assert decision.action == "SHORT"


def test_invalid_short_price_order_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TradingDecision(
            action="SHORT",
            confidence=0.8,
            entry_price=100,
            stop_loss=95,
            take_profit=110,
            allocation_pct=20,
            leverage=2,
            rationale="invalid short setup (LONG-style ordering)",
        )


def test_trade_decision_requires_allocation_pct() -> None:
    with pytest.raises(ValidationError):
        TradingDecision(
            action="LONG",
            confidence=0.8,
            entry_price=100,
            stop_loss=95,
            take_profit=110,
            leverage=1,
            rationale="missing allocation",
        )
