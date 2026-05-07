from coin_trading.market.collector import MarketDataCollector
from coin_trading.market.indicators import IndicatorCalculator
from coin_trading.market.news import NewsCollector
from coin_trading.market.news_coindesk import (
    CoinDeskNewsClient,
    CoinDeskNewsCollector,
)


def create_news_collector(settings):
    """Build a news collector based on settings.news_source."""
    if settings.news_source == "coindesk":
        client = CoinDeskNewsClient(
            api_key=settings.coindesk_api_key,
            lang=settings.coindesk_lang,
            categories=settings.coindesk_categories,
            exclude_categories=settings.coindesk_exclude_categories,
            timeout=settings.exchange_timeout_seconds,
        )
        return CoinDeskNewsCollector(client)
    return NewsCollector(settings.news_rss_urls)


__all__ = [
    "CoinDeskNewsClient",
    "CoinDeskNewsCollector",
    "IndicatorCalculator",
    "MarketDataCollector",
    "NewsCollector",
    "create_news_collector",
]
