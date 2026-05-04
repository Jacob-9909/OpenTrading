from typing import TypedDict
from coin_trading.agent.llm import TradingLLM
from coin_trading.agent.schemas import LLMResult

class AgentState(TypedDict):
    context: dict
    technical_report: str
    sentiment_report: str
    bull_argument: str
    bear_argument: str
    final_result: LLMResult | None
    llm: TradingLLM           # fund manager
    analyst_llm: TradingLLM   # technical + sentiment analyst
    researcher_llm: TradingLLM  # bull + bear researcher
