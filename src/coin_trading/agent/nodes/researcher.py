import logging

from coin_trading.agent.state import AgentState
from coin_trading.agent.prompts.researcher_prompts import (
    BULL_RESEARCHER_SYSTEM_PROMPT,
    BEAR_RESEARCHER_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


def sequential_debate_node(state: AgentState) -> dict:
    """Run Bull then Bear sequentially.

    Sequential by design: Bear must rebut Bull's specific argument, so it
    needs Bull's output as input. Despite appearing as a single LangGraph
    node, internally it is a two-step pipeline (Bull -> Bear), not a
    parallel debate.
    """
    logger.info("   -> [Node] Bullish Researcher is preparing arguments...")
    llm = state["researcher_llm"]
    tech = state.get("technical_report", "")
    sent = state.get("sentiment_report", "")

    bull_prompt = f"Technical Report:\n{tech}\n\nSentiment Report:\n{sent}"
    bull_arg = llm.chat(BULL_RESEARCHER_SYSTEM_PROMPT, bull_prompt)
    logger.info("[BULL ARGUMENT]\n%s", bull_arg)

    logger.info("   -> [Node] Bearish Researcher is attacking arguments...")
    bear_prompt = (
        f"Technical Report:\n{tech}\n\nSentiment Report:\n{sent}\n\n"
        f"Bull Argument (rebut this):\n{bull_arg}"
    )
    bear_arg = llm.chat(BEAR_RESEARCHER_SYSTEM_PROMPT, bear_prompt)
    logger.info("[BEAR ARGUMENT]\n%s", bear_arg)

    logger.info("   <- [Node] Debate concluded.")
    return {"bull_argument": bull_arg, "bear_argument": bear_arg}
