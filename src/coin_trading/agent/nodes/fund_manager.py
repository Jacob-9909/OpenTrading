from coin_trading.agent.state import AgentState

def fund_manager_node(state: AgentState) -> dict:
    print("   -> [Node] Fund Manager is reviewing all insights to decide...")
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
    print("\n" + "="*50)
    print("💼 [FUND MANAGER DECISION]")
    print("="*50)
    import json
    print(json.dumps(result.decision.model_dump(), indent=2, ensure_ascii=False))
    print("="*50 + "\n")
    print("   <- [Node] Fund Manager has signed the final decision.")
    return {"final_result": result}
