import json
import logging
import time

from coin_trading.agent.state import AgentState
from coin_trading.agent.prompts.analyst_prompts import (
    TECHNICAL_ANALYST_SYSTEM_PROMPT,
    SENTIMENT_ANALYST_SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)


def technical_analyst_node(state: AgentState) -> dict:
    logger.info("   -> [Node] Technical Analyst is analyzing charts...")
    started = time.perf_counter()
    llm = state["analyst_llm"]
    context = state["context"]

    tech_context = {
        "symbol": context.get("symbol"),
        "timeframe": context.get("timeframe"),
        "latest_price": context.get("latest_price"),
        "technical_indicators": context.get("technical_indicators"),
        "multi_timeframe": context.get("multi_timeframe"),
        "recent_candles": context.get("recent_candles", [])[:10],
    }

    report = llm.chat(TECHNICAL_ANALYST_SYSTEM_PROMPT, json.dumps(tech_context, ensure_ascii=False))
    elapsed = time.perf_counter() - started
    logger.info("[TECHNICAL REPORT]\n%s", report)
    logger.info("   <- [Node] Technical Analyst finished in %.2fs.", elapsed)
    return {"technical_report": report}


def sentiment_analyst_node(state: AgentState) -> dict:
    logger.info("   -> [Node] Sentiment Analyst is reading news...")
    started = time.perf_counter()
    llm = state["analyst_llm"]
    context = state["context"]

    news_context = {
        "symbol": context.get("symbol"),
        "news": context.get("news", []),
    }

    report = llm.chat(SENTIMENT_ANALYST_SYSTEM_PROMPT, json.dumps(news_context, ensure_ascii=False))
    elapsed = time.perf_counter() - started
    logger.info("[SENTIMENT REPORT]\n%s", report)
    logger.info("   <- [Node] Sentiment Analyst finished in %.2fs.", elapsed)
    return {"sentiment_report": report}
