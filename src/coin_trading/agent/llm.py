import json
import logging
from abc import ABC, abstractmethod

from pydantic import ValidationError

from coin_trading.config import Settings
from coin_trading.agent.prompts.fund_manager_prompts import (
    CRYPTO_FUND_MANAGER_SYSTEM_PROMPT as _SYSTEM_PROMPT_CRYPTO,
    STOCK_FUND_MANAGER_SYSTEM_PROMPT as _SYSTEM_PROMPT_STOCK,
    get_system_prompt,
)
from coin_trading.agent.schemas import LLMResult, TradingDecision

logger = logging.getLogger(__name__)

# Default kept for backward compatibility with existing tests
SYSTEM_PROMPT = _SYSTEM_PROMPT_CRYPTO


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

    # Normalize action to uppercase
    action = normalized.get("action")
    if isinstance(action, str):
        normalized["action"] = action.upper()

    risk_notes = normalized.get("risk_notes")
    if isinstance(risk_notes, str):
        normalized["risk_notes"] = [risk_notes]
    elif risk_notes is None:
        normalized["risk_notes"] = []
    elif isinstance(risk_notes, list):
        normalized["risk_notes"] = [str(item) for item in risk_notes]

    th = normalized.get("time_horizon")
    if th is None or (isinstance(th, str) and not th.strip()):
        normalized["time_horizon"] = "batch"
    elif not isinstance(th, str):
        normalized["time_horizon"] = str(th).strip() or "batch"

    # Fix price ordering for SHORT: schema requires take_profit < entry_price < stop_loss.
    # LLMs often return LONG-style ordering (stop_loss < entry_price < take_profit) for SHORT.
    # When detected, swap stop_loss and take_profit so the ordering is correct.
    if normalized.get("action") == "SHORT":
        ep = normalized.get("entry_price")
        sl = normalized.get("stop_loss")
        tp = normalized.get("take_profit")
        if ep is not None and sl is not None and tp is not None:
            try:
                ep_f, sl_f, tp_f = float(ep), float(sl), float(tp)
                # LONG-style: sl < ep < tp — swap to get tp < ep < sl
                if sl_f < ep_f < tp_f:
                    normalized["stop_loss"] = tp_f
                    normalized["take_profit"] = sl_f
            except (TypeError, ValueError):
                pass

    # Fix price ordering for LONG: schema requires stop_loss < entry_price < take_profit.
    # If reversed (tp < ep < sl), swap stop_loss and take_profit.
    if normalized.get("action") == "LONG":
        ep = normalized.get("entry_price")
        sl = normalized.get("stop_loss")
        tp = normalized.get("take_profit")
        if ep is not None and sl is not None and tp is not None:
            try:
                ep_f, sl_f, tp_f = float(ep), float(sl), float(tp)
                # Fully reversed: tp < ep < sl
                if tp_f < ep_f < sl_f:
                    normalized["stop_loss"] = tp_f
                    normalized["take_profit"] = sl_f
            except (TypeError, ValueError):
                pass

    return normalized


def _enforce_min_sltp(payload: dict, price: float, atr: float) -> dict:
    """Enforce minimum SL/TP distances. Expands tight levels to meet minimums."""
    action = payload.get("action")
    if action not in ("LONG", "SHORT"):
        return payload

    ep = payload.get("entry_price")
    sl = payload.get("stop_loss")
    tp = payload.get("take_profit")
    if ep is None or sl is None or tp is None:
        return payload

    try:
        ep_f, sl_f, tp_f = float(ep), float(sl), float(tp)
    except (TypeError, ValueError):
        return payload

    min_sl_dist = max(atr * 1.5, ep_f * 0.003)
    min_tp_dist = max(atr * 3.0, ep_f * 0.006)

    result = dict(payload)
    if action == "LONG":
        if (ep_f - sl_f) < min_sl_dist:
            result["stop_loss"] = round(ep_f - min_sl_dist, 8)
        if (tp_f - ep_f) < min_tp_dist:
            result["take_profit"] = round(ep_f + min_tp_dist, 8)
    elif action == "SHORT":
        if (sl_f - ep_f) < min_sl_dist:
            result["stop_loss"] = round(ep_f + min_sl_dist, 8)
        if (ep_f - tp_f) < min_tp_dist:
            result["take_profit"] = round(ep_f - min_tp_dist, 8)

    return result


def _decision_or_hold(provider: str, model: str, payload: dict) -> TradingDecision:
    normalized = _normalize_payload(payload)
    try:
        return TradingDecision.model_validate(normalized)
    except ValidationError as exc:
        logger.warning("[schema] ValidationError (%s/%s): %s", provider, model, exc.errors())
        logger.warning("[schema] normalized payload: %s", normalized)
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

        if trend == "bullish" and 30 <= rsi <= 65:
            decision = TradingDecision(
                action="LONG",
                confidence=0.62,
                entry_price=price,
                stop_loss=max(price - (atr * 1.5), price * 0.97),
                take_profit=price + (atr * 2.2),
                allocation_pct=min(max_allocation_pct, 15.0),
                leverage=leverage,
                rationale="Mock rule: bullish trend with RSI below overbought zone.",
                risk_notes=["Paper trading decision. Validate with live liquidity before production."],
            )
        elif trend == "bearish" and 35 <= rsi <= 70:
            decision = TradingDecision(
                action="SHORT",
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
        system_prompt = get_system_prompt(context.get("exchange", "bithumb_spot"))
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        price = float(context.get("latest_price") or 0)
        atr = float((context.get("technical_indicators") or {}).get("atr_14") or price * 0.02)
        payload = _enforce_min_sltp(payload, price, atr)
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
        system_prompt = get_system_prompt(context.get("exchange", "bithumb_spot"))
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
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
        price = float(context.get("latest_price") or 0)
        atr = float((context.get("technical_indicators") or {}).get("atr_14") or price * 0.02)
        payload = _enforce_min_sltp(payload, price, atr)
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
        system_prompt = get_system_prompt(context.get("exchange", "bithumb_spot"))
        response = self.client.models.generate_content(
            model=self.model,
            contents=f"{system_prompt}\n\nContext:\n{json.dumps(context, ensure_ascii=False)}",
            config={"response_mime_type": "application/json"},
        )
        payload = json.loads(response.text or "{}")
        price = float(context.get("latest_price") or 0)
        atr = float((context.get("technical_indicators") or {}).get("atr_14") or price * 0.02)
        payload = _enforce_min_sltp(payload, price, atr)
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
        system_prompt = get_system_prompt(context.get("exchange", "bithumb_spot"))

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
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
        price = float(context.get("latest_price") or 0)
        atr = float((context.get("technical_indicators") or {}).get("atr_14") or price * 0.02)
        payload = _enforce_min_sltp(payload, price, atr)
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


def create_agent_llm(
    settings: Settings,
    provider_override: str | None,
    model_override: str | None,
) -> TradingLLM:
    """Create LLM with optional provider/model override; falls back to settings defaults."""
    if not provider_override and not model_override:
        return create_llm(settings)
    patched = settings.model_copy(update={
        "llm_provider": provider_override or settings.llm_provider,
        "llm_model": model_override or settings.llm_model,
    })
    return create_llm(patched)


def create_llm(settings: Settings) -> TradingLLM:
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        return OpenAITradingLLM(api_key=settings.openai_api_key, model=settings.llm_model)
    if settings.llm_provider == "gemini":
        if not settings.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_PROVIDER=gemini.")
        return GeminiTradingLLM(api_key=settings.gemini_api_key, model=settings.llm_model)
    if settings.llm_provider == "nvidia":
        if not settings.nvidia_api_key:
            raise ValueError("NVIDIA_API_KEY is required when LLM_PROVIDER=nvidia.")
        return NvidiaTradingLLM(
            api_key=settings.nvidia_api_key,
            model=settings.llm_model,
            base_url=settings.nvidia_base_url,
        )
    return MockTradingLLM()
