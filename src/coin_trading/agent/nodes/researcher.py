import logging
import time

from coin_trading.agent.state import AgentState
from coin_trading.agent.prompts.researcher_prompts import (
    BULL_RESEARCHER_SYSTEM_PROMPT,
    BEAR_RESEARCHER_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


def sequential_debate_node(state: AgentState) -> dict:
    """Run Bull and Bear independently on the same data.

    Both analysts receive identical input (technical + sentiment report only).
    Neither sees the other's argument, removing the structural last-word
    advantage that Bear had when it was allowed to rebut Bull directly.
    The Fund Manager receives both arguments and judges independently.
    """
    llm = state["researcher_llm"]
    tech = state.get("technical_report", "")
    sent = state.get("sentiment_report", "")
    shared_prompt = f"Technical Report:\n{tech}\n\nSentiment Report:\n{sent}"

    logger.info("   -> [Node] Bullish Researcher is preparing arguments...")
    bull_started = time.perf_counter()
    bull_arg = llm.chat(BULL_RESEARCHER_SYSTEM_PROMPT, shared_prompt)
    bull_elapsed = time.perf_counter() - bull_started
    logger.info("[BULL ARGUMENT]\n%s", bull_arg)
    logger.info("   <- Bull finished in %.2fs", bull_elapsed)

    logger.info("   -> [Node] Bearish Researcher is preparing arguments...")
    bear_started = time.perf_counter()
    bear_arg = llm.chat(BEAR_RESEARCHER_SYSTEM_PROMPT, shared_prompt)
    bear_elapsed = time.perf_counter() - bear_started
    logger.info("[BEAR ARGUMENT]\n%s", bear_arg)
    logger.info("   <- Bear finished in %.2fs", bear_elapsed)

    logger.info(
        "   <- [Node] Debate concluded (bull=%.2fs bear=%.2fs total=%.2fs).",
        bull_elapsed, bear_elapsed, bull_elapsed + bear_elapsed,
    )
    return {"bull_argument": bull_arg, "bear_argument": bear_arg}
