import pytest

from coin_trading.config import Settings
from coin_trading.llm.providers import OpenRouterTradingLLM, SYSTEM_PROMPT, create_llm


def test_openrouter_provider_requires_api_key() -> None:
    settings = Settings(
        llm_provider="openrouter",
        llm_model="google/gemma-4-31b-it:free",
        openrouter_api_key=None,
    )

    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        create_llm(settings)


def test_openrouter_provider_uses_configured_gemma_model() -> None:
    llm = create_llm(
        Settings(
            llm_provider="openrouter",
            llm_model="google/gemma-4-31b-it:free",
            openrouter_api_key="test-key",
        )
    )

    assert isinstance(llm, OpenRouterTradingLLM)
    assert llm.model == "google/gemma-4-31b-it:free"


class FakeRateLimitError(Exception):
    status_code = 429


class FakeCompletions:
    def create(self, **_kwargs):
        raise FakeRateLimitError("temporarily rate-limited upstream")


class FakeChat:
    completions = FakeCompletions()


class FakeClient:
    chat = FakeChat()


def test_openrouter_rate_limit_falls_back_to_hold() -> None:
    llm = object.__new__(OpenRouterTradingLLM)
    llm.client = FakeClient()
    llm.model = "google/gemma-4-31b-it:free"

    result = llm.decide({"latest_price": 100})

    assert result.decision.action == "HOLD"
    assert result.decision.confidence == 0
    assert "rate-limited" in result.decision.rationale
    assert result.raw_response["error_type"] == "FakeRateLimitError"


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [FakeChoice(content)]
        self.usage = None


class FakeCompletionsWithPayload:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def create(self, **_kwargs):
        return FakeResponse(content=__import__("json").dumps(self.payload))


class FakeChatWithPayload:
    def __init__(self, payload: dict) -> None:
        self.completions = FakeCompletionsWithPayload(payload)


class FakeClientWithPayload:
    def __init__(self, payload: dict) -> None:
        self.chat = FakeChatWithPayload(payload)


def test_openrouter_normalizes_string_risk_notes() -> None:
    payload = {
        "action": "BUY",
        "confidence": 0.8,
        "entry_price": 100,
        "stop_loss": 95,
        "take_profit": 110,
        "allocation_pct": 10,
        "leverage": 1,
        "rationale": "valid",
        "risk_notes": "single note",
    }
    llm = object.__new__(OpenRouterTradingLLM)
    llm.client = FakeClientWithPayload(payload)
    llm.model = "google/gemma-4-31b-it:free"

    result = llm.decide({"latest_price": 100})

    assert result.decision.action == "BUY"
    assert result.decision.risk_notes == ["single note"]


def test_openrouter_normalizes_null_time_horizon() -> None:
    payload = {
        "action": "BUY",
        "confidence": 0.8,
        "entry_price": 100,
        "stop_loss": 95,
        "take_profit": 110,
        "allocation_pct": 10,
        "leverage": 1,
        "time_horizon": None,
        "rationale": "valid",
        "risk_notes": [],
    }
    llm = object.__new__(OpenRouterTradingLLM)
    llm.client = FakeClientWithPayload(payload)
    llm.model = "google/gemma-4-31b-it:free"

    result = llm.decide({"latest_price": 100})

    assert result.decision.action == "BUY"
    assert result.decision.time_horizon == "batch"


def test_openrouter_normalizes_blank_time_horizon() -> None:
    payload = {
        "action": "BUY",
        "confidence": 0.8,
        "entry_price": 100,
        "stop_loss": 95,
        "take_profit": 110,
        "allocation_pct": 10,
        "leverage": 1,
        "time_horizon": "   ",
        "rationale": "valid",
        "risk_notes": [],
    }
    llm = object.__new__(OpenRouterTradingLLM)
    llm.client = FakeClientWithPayload(payload)
    llm.model = "google/gemma-4-31b-it:free"

    result = llm.decide({"latest_price": 100})

    assert result.decision.action == "BUY"
    assert result.decision.time_horizon == "batch"


def test_system_prompt_enforces_position_aware_rules_and_risk_notes_array() -> None:
    assert "base_asset_quantity" in SYSTEM_PROMPT
    assert "risk_notes" in SYSTEM_PROMPT
    assert "array of strings" in SYSTEM_PROMPT
    assert "Never set allocation_pct above portfolio.max_position_allocation_pct." in SYSTEM_PROMPT
    assert "take_profit < entry_price < stop_loss" in SYSTEM_PROMPT
    assert "never null" in SYSTEM_PROMPT.lower()
