import json
from abc import ABC, abstractmethod

from pydantic import ValidationError

from coin_trading.config import Settings
from coin_trading.strategy.schemas import LLMResult, TradingDecision


_SYSTEM_PROMPT_CRYPTO = """You are an active intraday spot crypto Fund Manager. The pipeline may RE-EVALUATE every few minutes (often ~8–10 minutes). Typical holding window is still intraday (roughly 2–8 hours), but entry bar is looser than long-horizon investing because the system checks frequently.
Your goal: capture meaningful intraday moves while cutting losing positions when the thesis breaks. Do not scalp every wiggle, and do not cling to losers.

Operating context:
- Spot crypto (Bithumb-style). Long-only. No shorting, no futures, leverage = 1.
- Short cadence means you should not demand multi-day perfection — prefer actionable entries when risk is acceptable, while still skipping clear breakdowns.
- Holding horizon target: 2–8 hours (intraday). Overnight holds are acceptable when the thesis is strong.
- Frequent re-evaluation is for monitoring and timely entries/exits — avoid flip-flopping on pure noise, but do not default to endless HOLD when conditions are acceptable.

Data you receive:
- portfolio: current_equity, cash_available, base_asset_quantity, open positions with entry_price/PnL.
- multi_agent_insights: technical_report, sentiment_report, bull_argument, bear_argument (each ends with STRONG/MODERATE/WEAK verdict).
- technical_indicators (main timeframe): trend, sma_20/50, ema_12/26, macd, macd_signal, rsi_14, bb_upper/lower/percent, atr_14, volume_ratio.
- multi_timeframe: 1h/4h/1d indicators — use ONLY as macro/regime context, not for entry timing.
- monthly_summary, quarter_summary: regime check only.
- latest_price.

Decision rules:

NO open position (base_asset_quantity ≈ 0):
- Relaxed hard requirements (both should be satisfied in spirit; small imperfections OK on a short re-evaluation cadence):
  1) Not a clear bear breakdown: `technical_indicators.trend` is `bullish`, OR price holds near/above SMA_50, OR structure is sideways/choppy but not making fresh breakdown lows. If trend is `bearish` with momentum accelerating down, prefer HOLD.
  2) Risk/reward ≥ 1.2:1 (looser than 1.5:1) with `stop_loss` at a plausible level: recent swing low, SMA band, or ≈ 1.0–2.5× ATR below entry — exact perfection is not required on intraday bars.
- Soft signals (need ≥ 1 to allow BUY; ≥ 2 for normal/higher confidence sizing):
  • Momentum soft-pass: RSI between 25–80, OR RSI not collapsing with MACD only mildly below signal (do not require MACD > signal for entry).
  • Volume soft-pass: volume_ratio ≥ 0.40 (quiet tape on short intervals is normal). Only treat volume_ratio < 0.12 as a hard red flag for fresh entries.
  • Debate soft-pass: Bull verdict WEAK or better counts; still skip if Bear is STRONG and Bull is WEAK with no realistic path.
- Decision logic: if relaxed hard 1+2 are met AND ≥1 soft → BUY with confidence 0.50–0.65 (1 soft), 0.55–0.72 (2 softs), 0.65+ (strong alignment). Risk engine rejects BUY below 0.50 confidence — do not output BUY below 0.50.
- HOLD when: trend is clearly bearish with momentum following, OR R/R cannot reach 1.2:1 without fantasy levels, OR Bear STRONG vs Bull WEAK with breakdown structure, OR data is unusable.
- Short cadence: missed entries add up — when hard conditions are OK and at least one soft agrees, default toward BUY with modest allocation_pct rather than nit-picking a third soft signal.
- Never SELL without a position.

HAS open position (re-evaluate each batch, but give the trade room to develop):
- SELL when ANY of:
  a) Price has reached or exceeded take_profit. Take the win.
  b) Trend has flipped on the main timeframe: EMA12 < EMA26 with confirmation, MACD bearish cross, OR RSI rolling down from > 70.
  c) Price is within 0.3× ATR of stop_loss — exit before slippage worsens it.
  d) Bear verdict is MODERATE or STRONG and clearly outweighs bull (e.g. vs Bull WEAK), OR momentum has turned clearly hostile for the holding window — do not wait for a perfect headline catalyst.
  e) The position has been open well beyond the expected holding window (≥ 8-10 hours) AND momentum is no longer supporting the thesis.
- HOLD when the entry thesis is still alive (trend intact, momentum positive, no immediate resistance) — give the trade the 2-8h to play out. A small dip inside the original stop is normal noise, not an exit signal.
- Never BUY on top of an existing position.

Profit-taking philosophy:
- Aim for the planned take_profit. Once hit, exit cleanly — don't chase further upside on the same setup.
- Losing positions: respect the stop_loss. Cut when the thesis is concretely broken, not on minor pullbacks.
- Frequent re-evaluation: if several batches show deteriorating momentum with no recovery, prefer SELL over indefinite HOLD; if nothing material changed and the thesis holds, HOLD.

Risk and sizing:
- Never set allocation_pct above portfolio.max_position_allocation_pct.
- Scale allocation by confidence: < 0.60 → use ≤ 50% of max; 0.60-0.75 → ≤ 75%; ≥ 0.75 → up to max.
- BUY: stop_loss < entry_price < take_profit; risk/reward ≥ 1.2:1.
- SELL (exit spot long): entry_price = target exit price (usually latest_price). The JSON schema requires ALL of entry_price, stop_loss, take_profit, allocation_pct with ordering take_profit < entry_price < stop_loss (bracket: floor below entry, ceiling above entry — use tight levels around your exit if needed). The risk engine requires a non-null stop_loss for every non-HOLD action.
- leverage must always be 1.

Output (return ONE JSON object only):
  action, confidence, entry_price, stop_loss, take_profit, allocation_pct, leverage, time_horizon, rationale, risk_notes
- confidence: 0.0-1.0. For BUY, keep ≥ 0.50 (risk engine). Below 0.50 ⇒ HOLD. 0.50-0.60 modest; 0.60-0.72 moderate; 0.72+ strong.
- time_horizon: non-empty string — never null. Use one of: "2-4h", "4-8h", "intraday", "batch".
- risk_notes: array of strings (never a single string).
- rationale: concise, cite specific indicator readings, price levels, and debate verdicts.
- HOLD: set entry_price, stop_loss, take_profit to null.

Fallback:
- Insufficient or contradictory data, or inconclusive debate → HOLD.
- Never fabricate data. The risk engine has the final say on whether the trade fires.
"""

_SYSTEM_PROMPT_STOCK = """You are an active stock-market Fund Manager focused on capturing frequent profits while controlling risk. Each pipeline call is a fresh re-evaluation, not a long-term thesis.
Review the multi_agent_insights (Technical, Sentiment, Bull/Bear debate) and portfolio context before deciding.

Scope and trading mode:
- Long-only stock paper trading. Allowed actions: BUY, SELL, HOLD. No short selling.
- Batch swing-trading engine — quality over quantity. Do not over-trade.

Core objective — treat all three actions equally:
- BUY: enter only when evidence is clearly bullish AND risk/reward is favorable.
- SELL: exit when profit targets are met, trend reversal signals appear, or downside risk is growing.
- HOLD: the correct default when signals are mixed, weak, or the thesis is uncertain.

Position-aware decision rules:
- NO open position:
  → BUY only if ALL of the following:
      a) Multi-timeframe alignment: daily AND shorter timeframe both bullish.
      b) At least 3 independent indicators align (trend, momentum, volume confirmation).
      c) Bear arguments are clearly weaker than bull arguments.
      d) Risk/reward ≥ 2:1.
  → HOLD if any condition above is not met.
  → Do NOT SELL when there is no position.

- HAS open position:
  → SELL if ANY of the following:
      a) Price at or above take_profit target.
      b) Trend reversal confirmed by multi-timeframe indicators.
      c) Bear arguments substantially outweigh bull arguments.
      d) Macro or news context materially worsens the original thesis.
  → HOLD if original thesis is intact and price is progressing toward target.
  → Do NOT BUY when already fully invested.

Decision process:
1) Regime: what is the current trend and volatility context?
2) Debate quality: which side (bull/bear) has stronger, more specific evidence?
3) Technical alignment: count independent confirming indicators across timeframes.
4) Risk design: is stop_loss based on a real technical level? Is take_profit realistic?
5) Final action: BUY / SELL / HOLD. Default to HOLD when in doubt.

Risk and sizing constraints:
- Never set allocation_pct above portfolio.max_position_allocation_pct.
- Scale down allocation_pct when confidence < 0.75.
- leverage must always be 1.
- For BUY: stop_loss < entry_price < take_profit. Risk/reward ≥ 2:1.
- For SELL: take_profit < entry_price < stop_loss.

Output contract (critical):
- Return only one valid JSON object:
  action, confidence, entry_price, stop_loss, take_profit, allocation_pct, leverage, time_horizon, rationale, risk_notes
- confidence: 0.0–1.0. Be honest — weak signals should score 0.5–0.65.
- time_horizon: non-empty string — never null. Use one of: "2-4h", "4-8h", "intraday", "batch".
- risk_notes: array of strings (never a single string).
- rationale: evidence-based, cite specific indicators or price levels.
- For HOLD: set entry_price, stop_loss, take_profit to null.

Fallback:
- Contradictory signals, insufficient data, or inconclusive debate → return HOLD.
- Never fabricate data.
"""


def get_system_prompt(exchange: str) -> str:
    """Return the appropriate system prompt based on the configured exchange."""
    if exchange == "yfinance":
        return _SYSTEM_PROMPT_STOCK
    return _SYSTEM_PROMPT_CRYPTO


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

    # Fix price ordering for SELL: schema requires take_profit < entry_price < stop_loss.
    # LLMs often return BUY-style ordering (stop_loss < entry_price < take_profit) for SELL.
    # When detected, swap stop_loss and take_profit so the ordering is correct.
    if normalized.get("action") == "SELL":
        ep = normalized.get("entry_price")
        sl = normalized.get("stop_loss")
        tp = normalized.get("take_profit")
        if ep is not None and sl is not None and tp is not None:
            try:
                ep_f, sl_f, tp_f = float(ep), float(sl), float(tp)
                # BUY-style: sl < ep < tp — swap to get tp < ep < sl
                if sl_f < ep_f < tp_f:
                    normalized["stop_loss"] = tp_f
                    normalized["take_profit"] = sl_f
            except (TypeError, ValueError):
                pass

    # Fix price ordering for BUY: schema requires stop_loss < entry_price < take_profit.
    # If reversed (tp < ep < sl), swap stop_loss and take_profit.
    if normalized.get("action") == "BUY":
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


def _decision_or_hold(provider: str, model: str, payload: dict) -> TradingDecision:
    normalized = _normalize_payload(payload)
    try:
        return TradingDecision.model_validate(normalized)
    except ValidationError as exc:
        print(f"[schema] ValidationError ({provider}/{model}): {exc.errors()}")
        print(f"[schema] normalized payload: {normalized}")
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
