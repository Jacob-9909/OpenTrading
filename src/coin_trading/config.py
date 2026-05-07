from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        enable_decoding=False,
    )

    app_env: str = "local"
    database_url: str = "sqlite:///./coin_trading.db"
    trading_mode: Literal["paper", "live", "signal_only"] = "paper"
    portfolio_source: Literal["paper", "exchange"] = "paper"
    live_trading_enabled: bool = False
    live_order_type: Literal["limit", "market"] = "limit"
    live_min_order_krw: float = Field(default=5_000, gt=0)
    live_max_order_krw: float = Field(default=100_000, gt=0)
    exchange: Literal["bithumb_spot", "binance_futures", "yfinance"] = "bithumb_spot"
    symbol: str = "KRW-BTC"
    timeframe: str = "10m"
    lookback_limit: int = Field(default=500, ge=50, le=1500)
    analysis_timeframes: list[str] = Field(default_factory=lambda: ["10m", "30m", "1h", "4h", "1d"])
    recent_candle_limit: int = Field(default=60, ge=30, le=120)
    dashboard_chart_timeframe: str = "10m"
    dashboard_chart_days: int = Field(default=10, ge=1, le=90)

    initial_equity: float | None = Field(default=None, gt=0)
    risk_per_trade: float = Field(default=0.01, gt=0, le=0.10)
    max_leverage: int = Field(default=3, ge=1, le=125)
    max_position_allocation_pct: float = Field(default=30.0, gt=0, le=100)
    liquidation_buffer: float = Field(default=0.08, gt=0, le=0.50)
    kill_switch_drawdown: float = Field(default=0.10, gt=0, le=0.90)

    llm_provider: Literal["mock", "openai", "gemini", "openrouter", "nvidia"] = "mock"
    llm_model: str = "gpt-4o-mini"
    analyst_llm_provider: str | None = None   # falls back to llm_provider if unset
    analyst_llm_model: str | None = None      # falls back to llm_model if unset
    researcher_llm_provider: str | None = None
    researcher_llm_model: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    nvidia_api_key: str | None = None
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"

    binance_api_key: str | None = None
    binance_api_secret: str | None = None
    binance_base_url: str = "https://fapi.binance.com"

    bithumb_access_key: str | None = None
    bithumb_secret_key: str | None = None
    bithumb_base_url: str = "https://api.bithumb.com"

    # 시장 데이터 수집
    max_candles_per_fetch: int = Field(default=200, ge=1)
    backfill_max_pages: int = Field(default=20, ge=1)
    news_context_limit: int = Field(default=8, ge=1)

    # 기술 지표
    indicator_min_candles: int = Field(default=50, ge=10)

    # HTTP
    exchange_timeout_seconds: float = Field(default=10.0, gt=0)

    news_rss_urls: list[str] = Field(
        default_factory=lambda: [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss",
        ]
    )
    run_once_interval_minutes: int = Field(default=360, ge=1)
    scheduler_timezone: str = "Asia/Seoul"
    decision_cooldown_minutes: int = Field(default=0, ge=0)
    max_data_staleness_minutes: int = Field(default=30, ge=1)
    reentry_cooldown_minutes: int = Field(default=0, ge=0)
    trailing_stop_pct: float | None = Field(default=None, gt=0, le=0.5)
    trailing_tp_pct: float | None = Field(default=None, gt=0, le=0.5)
    price_consistency_threshold_pct: float = Field(default=0.5, ge=0.0)

    # Slack / Gemini Vertex AI 알림
    slack_webhook_url: str | None = None
    vertex_project_id: str | None = None
    vertex_model_id: str = "gemini-2.5-flash"
    vertex_location: str = "us-central1"
    google_application_credentials: str | None = None

    @field_validator("news_rss_urls", "analysis_timeframes", mode="before")
    @classmethod
    def split_csv_values(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return value
        return []

    @property
    def is_live_trading(self) -> bool:
        return self.trading_mode == "live"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
