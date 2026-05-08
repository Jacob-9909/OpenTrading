import logging
import time

from coin_trading.agent.state import AgentState
from coin_trading.agent.prompts.researcher_prompts import (
    BULL_RESEARCHER_SYSTEM_PROMPT,
    BEAR_RESEARCHER_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


def _shared_prompt(state: AgentState) -> str:
    tech = state.get("technical_report", "")
    sent = state.get("sentiment_report", "")
    return f"Technical Report:\n{tech}\n\nSentiment Report:\n{sent}"


def bull_researcher_node(state: AgentState) -> dict:
    logger.info("   -> [Node] Bullish Researcher is preparing arguments...")
    started = time.perf_counter()
    arg = state["researcher_llm"].chat(BULL_RESEARCHER_SYSTEM_PROMPT, _shared_prompt(state))
    logger.info("[BULL ARGUMENT]\n%s", arg)
    logger.info("   <- Bull finished in %.2fs", time.perf_counter() - started)
    return {"bull_argument": arg}


def bear_researcher_node(state: AgentState) -> dict:
    logger.info("   -> [Node] Bearish Researcher is preparing arguments...")
    started = time.perf_counter()
    arg = state["researcher_llm"].chat(BEAR_RESEARCHER_SYSTEM_PROMPT, _shared_prompt(state))
    logger.info("[BEAR ARGUMENT]\n%s", arg)
    logger.info("   <- Bear finished in %.2fs", time.perf_counter() - started)
    return {"bear_argument": arg}
