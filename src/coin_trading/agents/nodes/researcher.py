from coin_trading.agents.state import AgentState
from coin_trading.agents.prompts.researcher_prompts import (
    BULL_RESEARCHER_SYSTEM_PROMPT,
    BEAR_RESEARCHER_SYSTEM_PROMPT,
)

def researcher_debate_node(state: AgentState) -> dict:
    print("   -> [Node] Bullish Researcher is preparing arguments...")
    llm = state["llm"]
    tech = state.get("technical_report", "")
    sent = state.get("sentiment_report", "")
    
    bull_prompt = f"Technical Report:\n{tech}\n\nSentiment Report:\n{sent}"
    bull_arg = llm.chat(BULL_RESEARCHER_SYSTEM_PROMPT, bull_prompt)
    print("\n" + "="*50)
    print("🐂 [BULL ARGUMENT]")
    print("="*50)
    print(bull_arg)
    print("="*50 + "\n")
    
    print("   -> [Node] Bearish Researcher is attacking arguments...")
    bear_arg = llm.chat(BEAR_RESEARCHER_SYSTEM_PROMPT, bull_prompt)
    print("\n" + "="*50)
    print("🐻 [BEAR ARGUMENT]")
    print("="*50)
    print(bear_arg)
    print("="*50 + "\n")
    
    print("   <- [Node] Debate concluded.")
    
    return {"bull_argument": bull_arg, "bear_argument": bear_arg}
