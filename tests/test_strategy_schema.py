import pytest
from pydantic import ValidationError

from coin_trading.strategy.schemas import TradingDecision


def test_buy_decision_requires_stop_below_entry_and_target_above() -> None:
    decision = TradingDecision(
        action="BUY",
        confidence=0.8,
        entry_price=100,
        stop_loss=95,
        take_profit=110,
        allocation_pct=20,
        leverage=2,
        rationale="valid setup",
    )

    assert decision.action == "BUY"


def test_invalid_sell_price_order_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TradingDecision(
            action="SELL",
            confidence=0.8,
            entry_price=100,
            stop_loss=95,
            take_profit=110,
            allocation_pct=20,
            leverage=2,
            rationale="invalid setup",
        )


def test_trade_decision_requires_allocation_pct() -> None:
    with pytest.raises(ValidationError):
        TradingDecision(
            action="BUY",
            confidence=0.8,
            entry_price=100,
            stop_loss=95,
            take_profit=110,
            leverage=1,
            rationale="missing allocation",
        )
