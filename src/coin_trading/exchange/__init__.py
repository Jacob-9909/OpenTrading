from coin_trading.config import Settings
from coin_trading.exchange.base import Candle, MarketDataClient
from coin_trading.exchange.binance import BinanceFuturesClient
from coin_trading.exchange.bithumb import BithumbSpotClient


def create_exchange_client(settings: Settings) -> MarketDataClient:
    if settings.exchange == "binance_futures":
        return BinanceFuturesClient(base_url=settings.binance_base_url)
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
    "create_exchange_client",
]
