import json
from coin_trading.agents.state import AgentState
from coin_trading.agents.prompts.analyst_prompts import (
    TECHNICAL_ANALYST_SYSTEM_PROMPT,
    SENTIMENT_ANALYST_SYSTEM_PROMPT,
)

def technical_analyst_node(state: AgentState) -> dict:
    print("   -> [Node] Technical Analyst is analyzing charts...")
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
    print("\n" + "="*50)
    print("📈 [TECHNICAL REPORT]")
    print("="*50)
    print(report)
    print("="*50 + "\n")
    print("   <- [Node] Technical Analyst finished.")
    return {"technical_report": report}

def sentiment_analyst_node(state: AgentState) -> dict:
    print("   -> [Node] Sentiment Analyst is reading news...")
    llm = state["analyst_llm"]
    context = state["context"]

    news_context = {
        "symbol": context.get("symbol"),
        "news": context.get("news", [])
    }

    report = llm.chat(SENTIMENT_ANALYST_SYSTEM_PROMPT, json.dumps(news_context, ensure_ascii=False))
    print("\n" + "="*50)
    print("📰 [SENTIMENT REPORT]")
    print("="*50)
    print(report)
    print("="*50 + "\n")
    print("   <- [Node] Sentiment Analyst finished.")
    return {"sentiment_report": report}
