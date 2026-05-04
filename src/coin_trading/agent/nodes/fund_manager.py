import json
import logging

from coin_trading.agent.state import AgentState

logger = logging.getLogger(__name__)


def fund_manager_node(state: AgentState) -> dict:
    logger.info("   -> [Node] Fund Manager is reviewing all insights to decide...")
    llm = state["llm"]
    context = state["context"]

    enriched_context = dict(context)
    enriched_context["multi_agent_insights"] = {
        "technical_analysis": state.get("technical_report"),
        "sentiment_analysis": state.get("sentiment_report"),
        "bull_researcher": state.get("bull_argument"),
        "bear_researcher": state.get("bear_argument"),
    }

    enriched_context.pop("recent_candles", None)
    enriched_context.pop("news", None)

    result = llm.decide(enriched_context)
    logger.info(
        "[FUND MANAGER DECISION]\n%s",
        json.dumps(result.decision.model_dump(), indent=2, ensure_ascii=False),
    )
    logger.info("   <- [Node] Fund Manager has signed the final decision.")
    return {"final_result": result}
