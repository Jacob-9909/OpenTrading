from coin_trading.config import Settings
from coin_trading.exchange.bithumb import BithumbSpotClient
from coin_trading.exchange.base import MarketDataClient
from coin_trading.execution.live_bithumb import BithumbLiveExecutor
from coin_trading.execution.paper import PaperExecutor


def create_executor(settings: Settings, client: MarketDataClient):
    if settings.trading_mode == "live":
        if not isinstance(client, BithumbSpotClient):
            raise ValueError("Live trading is implemented only for Bithumb spot.")
        return BithumbLiveExecutor(settings, client)
    return PaperExecutor()


__all__ = ["BithumbLiveExecutor", "PaperExecutor", "create_executor"]
