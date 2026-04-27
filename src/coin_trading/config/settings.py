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
    exchange: Literal["bithumb_spot", "binance_futures"] = "bithumb_spot"
    symbol: str = "KRW-BTC"
    timeframe: str = "1h"
    lookback_limit: int = Field(default=200, ge=50, le=1500)
    analysis_timeframes: list[str] = Field(default_factory=lambda: ["1h", "4h", "1d"])
    recent_candle_limit: int = Field(default=60, ge=30, le=120)
    dashboard_chart_timeframe: str = "10m"
    dashboard_chart_days: int = Field(default=10, ge=1, le=90)

    initial_equity: float = Field(default=10_000, gt=0)
    risk_per_trade: float = Field(default=0.01, gt=0, le=0.10)
    daily_max_loss: float = Field(default=0.03, gt=0, le=0.50)
    max_leverage: int = Field(default=3, ge=1, le=125)
    max_open_positions: int = Field(default=1, ge=1, le=10)
    max_position_allocation_pct: float = Field(default=30.0, gt=0, le=100)
    liquidation_buffer: float = Field(default=0.08, gt=0, le=0.50)
    kill_switch_drawdown: float = Field(default=0.10, gt=0, le=0.90)

    llm_provider: Literal["mock", "openai", "gemini", "openrouter", "nvidia"] = "mock"
    llm_model: str = "gpt-4o-mini"
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

    news_rss_urls: list[str] = Field(
        default_factory=lambda: [
            "https://www.coindesk.com/arc/outboundfeeds/rss/",
            "https://cointelegraph.com/rss",
        ]
    )
    scheduler_interval_minutes: int = Field(default=60, ge=1)
    run_once_interval_minutes: int = Field(default=360, ge=1)
    data_refresh_interval_minutes: int = Field(default=240, ge=1)
    scheduler_timezone: str = "Asia/Seoul"
    decision_times: list[str] = Field(default_factory=lambda: ["09:00"])
    decision_interval_minutes: int = Field(default=60, ge=1)
    decision_cooldown_minutes: int = Field(default=1440, ge=0)
    max_data_staleness_minutes: int = Field(default=180, ge=1)

    @field_validator("news_rss_urls", "analysis_timeframes", "decision_times", mode="before")
    @classmethod
    def split_csv_values(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return value
        return []

    @field_validator("decision_times")
    @classmethod
    def validate_decision_times(cls, value: list[str]) -> list[str]:
        for item in value:
            hour, separator, minute = item.partition(":")
            if separator != ":" or not hour.isdigit() or not minute.isdigit():
                raise ValueError("decision_times must use HH:MM format.")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError("decision_times must use valid 24-hour HH:MM values.")
        return value

    @property
    def is_live_trading(self) -> bool:
        return self.trading_mode == "live"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
