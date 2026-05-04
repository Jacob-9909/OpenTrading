from coin_trading.config import Settings
from coin_trading.market.exchange.base import Candle, MarketDataClient
from coin_trading.market.exchange.binance import BinanceFuturesClient
from coin_trading.market.exchange.bithumb import BithumbSpotClient
from coin_trading.market.exchange.yfinance_client import YFinanceClient


def create_exchange_client(settings: Settings) -> MarketDataClient:
    if settings.exchange == "binance_futures":
        return BinanceFuturesClient(base_url=settings.binance_base_url)
    if settings.exchange == "yfinance":
        return YFinanceClient()
    return BithumbSpotClient(
        access_key=settings.bithumb_access_key,
        secret_key=settings.bithumb_secret_key,
        base_url=settings.bithumb_base_url,
    )


__all__ = [
    "BinanceFuturesClient",
    "BithumbSpotClient",
    "Candle",
    "MarketDataClient",
    "YFinanceClient",
    "create_exchange_client",
]
