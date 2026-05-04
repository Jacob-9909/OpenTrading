from coin_trading.config import Settings
from coin_trading.market.exchange.bithumb import BithumbSpotClient
from coin_trading.market.exchange.base import MarketDataClient
from coin_trading.trade.execution.base import BaseExecutor
from coin_trading.trade.execution.live_bithumb import BithumbLiveExecutor
from coin_trading.trade.execution.paper import PaperExecutor


def create_executor(settings: Settings, client: MarketDataClient) -> BaseExecutor:
    if settings.trading_mode == "live":
        if not isinstance(client, BithumbSpotClient):
            raise ValueError("Live trading is implemented only for Bithumb spot.")
        return BithumbLiveExecutor(settings, client)
    return PaperExecutor()


__all__ = ["BaseExecutor", "BithumbLiveExecutor", "PaperExecutor", "create_executor"]
