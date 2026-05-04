from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TradingDecision(BaseModel):
    action: Literal["LONG", "SHORT", "HOLD"]
    confidence: float = Field(ge=0, le=1)
    entry_price: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    take_profit: float | None = Field(default=None, gt=0)
    allocation_pct: float | None = Field(default=None, ge=0, le=100)
    leverage: int = Field(default=1, ge=1, le=125)
    time_horizon: str = "batch"
    rationale: str
    risk_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_trade_prices(self) -> "TradingDecision":
        if self.action == "HOLD":
            return self
        missing = [
            name
            for name, value in [
                ("entry_price", self.entry_price),
                ("stop_loss", self.stop_loss),
                ("take_profit", self.take_profit),
                ("allocation_pct", self.allocation_pct),
            ]
            if value is None
        ]
        if missing:
            raise ValueError(f"{self.action} decision requires {', '.join(missing)}")
        if self.action == "LONG" and not (self.stop_loss < self.entry_price < self.take_profit):
            raise ValueError("LONG requires stop_loss < entry_price < take_profit")
        if self.action == "SHORT" and not (self.take_profit < self.entry_price < self.stop_loss):
            raise ValueError("SHORT requires take_profit < entry_price < stop_loss (price above TP and below SL)")
        return self


class LLMResult(BaseModel):
    provider: str
    model: str
    decision: TradingDecision
    raw_response: dict
    token_usage: dict | None = None
