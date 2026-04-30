import json
from abc import ABC, abstractmethod

from pydantic import ValidationError

from coin_trading.config import Settings
from coin_trading.strategy.schemas import LLMResult, TradingDecision


SYSTEM_PROMPT = """You are a senior spot crypto Fund Manager.
Review the multi_agent_insights (Technical, Sentiment, and Researcher debates) along with portfolio context.
Scope and trading mode:
- This system trades Bithumb spot only. Allowed actions are BUY, SELL, HOLD.
- Never propose futures assumptions, short selling, or liquidation-based reasoning.
- This is a batch decision engine, not a scalping bot.

Core objective:
- Preserve capital first, then seek asymmetric opportunities.
- Prefer HOLD when signal quality is weak, market context is conflicting, or risk is unclear.

Decision process (follow in this order):
1) Review multi_agent_insights: weigh the bull vs bear arguments and technical/sentiment reports.
2) Portfolio fit: consider current exposure, cash available, and position concentration.
3) Risk design: set realistic stop_loss and take_profit around volatility.
4) Final action: choose BUY, SELL, or HOLD with confidence grounded in evidence from the agents.

Risk and sizing constraints:
- Use portfolio.current_equity and portfolio.cash_available when reasoning about allocation_pct.
- Never set allocation_pct above portfolio.max_position_allocation_pct.
- If uncertainty is elevated, reduce allocation or choose HOLD.
- For BUY, enforce stop_loss < entry_price < take_profit.
- For SELL, enforce take_profit < entry_price < stop_loss.

Output contract (critical):
- Return only one valid JSON object with these keys:
  action, confidence, entry_price, stop_loss, take_profit, allocation_pct, leverage, time_horizon, rationale, risk_notes
- confidence must be a number between 0 and 1.
- risk_notes must always be an array of strings (never a single string).
- rationale must be concise and evidence-based, referencing market/indicator context.
- For HOLD, omit price fields or set them to null.

Conservative behavior defaults:
- If data quality is insufficient or contradictory, return HOLD.
- Do not fabricate unavailable facts.
"""


class TradingLLM(ABC):
    provider: str
    model: str

    @abstractmethod
    def decide(self, context: dict) -> LLMResult:
        raise NotImplementedError

    @abstractmethod
    def chat(self, system: str, user: str) -> str:
        raise NotImplementedError


def _hold_result(provider: str, model: str, rationale: str, raw_response: dict) -> LLMResult:
    decision = TradingDecision(
        action="HOLD",
        confidence=0,
        rationale=rationale,
        risk_notes=["No order is allowed when the LLM provider is unavailable."],
    )
    return LLMResult(
        provider=provider,
        model=model,
        decision=decision,
        raw_response=raw_response,
        token_usage=None,
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    return status_code == 429 or exc.__class__.__name__ == "RateLimitError"


def _normalize_payload(payload: dict) -> dict:
    normalized = dict(payload)
    risk_notes = normalized.get("risk_notes")
    if isinstance(risk_notes, str):
        normalized["risk_notes"] = [risk_notes]
    elif risk_notes is None:
        normalized["risk_notes"] = []
    elif isinstance(risk_notes, list):
        normalized["risk_notes"] = [str(item) for item in risk_notes]
    return normalized


def _decision_or_hold(provider: str, model: str, payload: dict) -> TradingDecision:
    normalized = _normalize_payload(payload)
    try:
        return TradingDecision.model_validate(normalized)
    except ValidationError as exc:
        return _hold_result(
            provider,
            model,
            "LLM response schema mismatch; fallback to HOLD.",
            {
                "error": str(exc),
                "error_type": exc.__class__.__name__,
                "raw_response": payload,
                "normalized_response": normalized,
            },
        ).decision


class MockTradingLLM(TradingLLM):
    provider = "mock"

    def __init__(self, model: str = "mock-rule-engine") -> None:
        self.model = model

    def decide(self, context: dict) -> LLMResult:
        indicators = context.get("technical_indicators") or {}
        price = float(context["latest_price"])
        rsi = float(indicators.get("rsi_14") or 50)
        trend = indicators.get("trend")
        atr = float(indicators.get("atr_14") or price * 0.02)
        leverage = 1
        portfolio = context.get("portfolio") or {}
        max_allocation_pct = float(portfolio.get("max_position_allocation_pct") or 30)

        if trend == "bullish" and rsi < 70:
            decision = TradingDecision(
                action="BUY",
                confidence=0.62,
                entry_price=price,
                stop_loss=max(price - (atr * 1.5), price * 0.97),
                take_profit=price + (atr * 2.2),
                allocation_pct=min(max_allocation_pct, 15.0),
                leverage=leverage,
                rationale="Mock rule: bullish trend with RSI below overbought zone.",
                risk_notes=["Paper trading decision. Validate with live liquidity before production."],
            )
        elif trend == "bearish" and rsi > 30:
            decision = TradingDecision(
                action="SELL",
                confidence=0.58,
                entry_price=price,
                stop_loss=min(price + (atr * 1.5), price * 1.03),
                take_profit=price - (atr * 2.2),
                allocation_pct=min(max_allocation_pct, 100.0),
                leverage=leverage,
                rationale="Mock rule: bearish trend with RSI above oversold zone.",
                risk_notes=["Paper trading decision. Validate with live liquidity before production."],
            )
        else:
            decision = TradingDecision(
                action="HOLD",
                confidence=0.55,
                rationale="Mock rule: indicators are not aligned enough for a directional trade.",
                risk_notes=["No trade protects against ambiguous market conditions."],
            )

        raw = decision.model_dump()
        return LLMResult(provider=self.provider, model=self.model, decision=decision, raw_response=raw)

    def chat(self, system: str, user: str) -> str:
        return "Mock response for intermediate agent text."


class OpenAITradingLLM(TradingLLM):
    provider = "openai"

    def __init__(self, api_key: str, model: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key)
        self.model = model

    def decide(self, context: dict) -> LLMResult:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        decision = _decision_or_hold(self.provider, self.model, payload)
        usage = response.usage.model_dump() if response.usage else None
        return LLMResult(
            provider=self.provider,
            model=self.model,
            decision=decision,
            raw_response=payload,
            token_usage=usage,
        )

    def chat(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""

class OpenRouterTradingLLM(TradingLLM):
    provider = "openrouter"

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/local/coin-trading",
                "X-Title": "CoinTrading",
            },
        )
        self.model = model

    def decide(self, context: dict) -> LLMResult:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if _is_rate_limit_error(exc):
                return _hold_result(
                    self.provider,
                    self.model,
                    "OpenRouter provider is rate-limited; fallback to HOLD.",
                    {"error": str(exc), "error_type": exc.__class__.__name__},
                )
            raise
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        decision = _decision_or_hold(self.provider, self.model, payload)
        usage = response.usage.model_dump() if response.usage else None
        return LLMResult(
            provider=self.provider,
            model=self.model,
            decision=decision,
            raw_response=payload,
            token_usage=usage,
        )

    def chat(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class GeminiTradingLLM(TradingLLM):
    provider = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        from google import genai

        self.client = genai.Client(api_key=api_key)
        self.model = model

    def decide(self, context: dict) -> LLMResult:
        response = self.client.models.generate_content(
            model=self.model,
            contents=f"{SYSTEM_PROMPT}\n\nContext:\n{json.dumps(context, ensure_ascii=False)}",
            config={"response_mime_type": "application/json"},
        )
        payload = json.loads(response.text or "{}")
        decision = _decision_or_hold(self.provider, self.model, payload)
        return LLMResult(
            provider=self.provider,
            model=self.model,
            decision=decision,
            raw_response=payload,
            token_usage=None,
        )

    def chat(self, system: str, user: str) -> str:
        response = self.client.models.generate_content(
            model=self.model,
            contents=f"{system}\n\nUser:\n{user}",
        )
        return response.text or ""


class NvidiaTradingLLM(TradingLLM):
    provider = "nvidia"

    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def decide(self, context: dict) -> LLMResult:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                temperature=0.6,
                top_p=0.9,
                max_tokens=4096,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            if _is_rate_limit_error(exc):
                return _hold_result(
                    self.provider,
                    self.model,
                    "NVIDIA provider is rate-limited; fallback to HOLD.",
                    {"error": str(exc), "error_type": exc.__class__.__name__},
                )
            raise

        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        decision = _decision_or_hold(self.provider, self.model, payload)
        usage = response.usage.model_dump() if response.usage else None
        return LLMResult(
            provider=self.provider,
            model=self.model,
            decision=decision,
            raw_response=payload,
            token_usage=usage,
        )

    def chat(self, system: str, user: str) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.6,
            top_p=0.9,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""


def create_llm(settings: Settings) -> TradingLLM:
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        return OpenAITradingLLM(api_key=settings.openai_api_key, model=settings.llm_model)
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini.")
        return GeminiTradingLLM(api_key=settings.gemini_api_key, model=settings.llm_model)
    if settings.llm_provider == "openrouter":
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required when LLM_PROVIDER=openrouter.")
        return OpenRouterTradingLLM(
            api_key=settings.openrouter_api_key,
            model=settings.llm_model,
            base_url=settings.openrouter_base_url,
        )
    if settings.llm_provider == "nvidia":
        if not settings.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is required when LLM_PROVIDER=nvidia.")
        return NvidiaTradingLLM(
            api_key=settings.nvidia_api_key,
            model=settings.llm_model,
            base_url=settings.nvidia_base_url,
        )
    return MockTradingLLM()
