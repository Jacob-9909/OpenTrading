from typing import TypedDict
from coin_trading.llm import TradingLLM
from coin_trading.strategy.schemas import LLMResult

class AgentState(TypedDict):
    context: dict
    technical_report: str
    sentiment_report: str
    bull_argument: str
    bear_argument: str
    final_result: LLMResult | None
    llm: TradingLLM
