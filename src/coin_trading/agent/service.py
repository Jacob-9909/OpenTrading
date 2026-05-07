import logging

from sqlalchemy.orm import Session

from coin_trading.agent import create_trading_agent_graph
from coin_trading.agent import TradingLLM
from coin_trading.agent.context import LLMContextBuilder
from coin_trading.agent.tracing import wrap_graph_with_opik
from coin_trading.config import Settings, get_settings
from coin_trading.db.models import LLMDecision, SignalSide, TradeSignal

logger = logging.getLogger(__name__)


class StrategyService:
    def __init__(
        self,
        llm: TradingLLM,
        context_builder: LLMContextBuilder,
        analyst_llm: TradingLLM | None = None,
        researcher_llm: TradingLLM | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.llm = llm
        self.analyst_llm = analyst_llm or llm
        self.researcher_llm = researcher_llm or llm
        self.context_builder = context_builder
        self.settings = settings or get_settings()
        # Compile graph once + wrap with Opik tracer (no-op if OPIK_API_KEY unset).
        self._agent_graph = wrap_graph_with_opik(
            create_trading_agent_graph(), self.settings
        )

    def create_signal(
        self,
        session: Session,
        symbol: str,
        timeframe: str,
        latest_price: float,
    ) -> TradeSignal:
        context = self.context_builder.build(session, symbol, timeframe, latest_price)

        logger.info("[Multi-Agent] Starting AI debate for %s...", symbol)
        initial_state = {
            "context": context,
            "llm": self.llm,
            "analyst_llm": self.analyst_llm,
            "researcher_llm": self.researcher_llm,
        }
        final_state = self._agent_graph.invoke(initial_state)
        llm_result = final_state["final_result"]
        logger.info("[Multi-Agent] Debate concluded and Fund Manager made a decision.")

        llm_decision = LLMDecision(
            provider=llm_result.provider,
            model=llm_result.model,
            prompt_summary=self.context_builder.summarize(context)
                + "\n[Multi-Agent] Used Technical, Sentiment, and Researcher nodes.",
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
            close_position_id=getattr(decision, "position_id", None),
            rationale=decision.rationale,
        )
        session.add(signal)
        session.commit()
        session.refresh(signal)
        signal.allocation_pct = decision.allocation_pct
        return signal
