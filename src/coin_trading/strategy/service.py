from sqlalchemy.orm import Session

from coin_trading.db.models import LLMDecision, SignalSide, TradeSignal
from coin_trading.llm import TradingLLM
from coin_trading.strategy.context import LLMContextBuilder


class StrategyService:
    def __init__(self, llm: TradingLLM, context_builder: LLMContextBuilder) -> None:
        self.llm = llm
        self.context_builder = context_builder

    def create_signal(
        self,
        session: Session,
        symbol: str,
        timeframe: str,
        latest_price: float,
    ) -> TradeSignal:
        context = self.context_builder.build(session, symbol, timeframe, latest_price)
        llm_result = self.llm.decide(context)
        llm_decision = LLMDecision(
            provider=llm_result.provider,
            model=llm_result.model,
            prompt_summary=self.context_builder.summarize(context),
            response=llm_result.raw_response,
            token_usage=llm_result.token_usage,
        )
        session.add(llm_decision)
        session.flush()

        decision = llm_result.decision
        signal = TradeSignal(
            llm_decision_id=llm_decision.id,
            symbol=symbol,
            side=SignalSide(decision.action),
            confidence=decision.confidence,
            entry_price=decision.entry_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            leverage=decision.leverage,
            rationale=decision.rationale,
        )
        session.add(signal)
        session.commit()
        session.refresh(signal)
        signal.allocation_pct = decision.allocation_pct
        return signal
