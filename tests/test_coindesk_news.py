import httpx
import pytest
import respx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from coin_trading.db.models import AppState, NewsItem
from coin_trading.db.session import Base
from coin_trading.market.news_coindesk import (
    CoinDeskNewsClient,
    CoinDeskNewsCollector,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as s:
        yield s


@respx.mock
def test_client_sends_api_key_and_params() -> None:
    route = respx.get("https://data-api.coindesk.com/news/v1/article/list").mock(
        return_value=httpx.Response(200, json={"Data": [], "Err": {}})
    )
    client = CoinDeskNewsClient(
        api_key="abc",
        lang="EN",
        categories=["BTC", "ETH"],
        exclude_categories=["NFT"],
    )
    client.fetch(limit=25, to_ts=1700000000)

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Apikey abc"
    assert "lang=EN" in request.url.query.decode()
    assert "limit=25" in request.url.query.decode()
    assert "categories=BTC%2CETH" in request.url.query.decode()
    assert "exclude_categories=NFT" in request.url.query.decode()
    assert "to_ts=1700000000" in request.url.query.decode()


@respx.mock
def test_collector_persists_articles_with_sentiment_mapping(session) -> None:
    respx.get("https://data-api.coindesk.com/news/v1/article/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "ID": 1,
                        "TITLE": "BTC breaks ATH",
                        "SUBTITLE": "Short summary line.",
                        "BODY": "Bitcoin reached a new all-time high.",
                        "URL": "https://example.com/1",
                        "PUBLISHED_ON": 1714000000,
                        "SENTIMENT": "POSITIVE",
                        "SCORE": 7,
                        "CATEGORY_DATA": [
                            {"NAME": "BTC", "CATEGORY": "BTC"},
                            {"NAME": "MARKET", "CATEGORY": "MARKET"},
                        ],
                        "SOURCE_DATA": {"NAME": "CoinDesk"},
                        "SOURCE_ID": 41,
                    },
                    {
                        "ID": 2,
                        "TITLE": "Hack drains protocol",
                        "BODY": "A DeFi protocol was exploited.",
                        "URL": "https://example.com/2",
                        "PUBLISHED_ON": 1714000100,
                        "SENTIMENT": "NEGATIVE",
                        "SOURCE_DATA": {"NAME": "Reuters"},
                    },
                    {
                        "ID": 3,
                        "TITLE": "no url",
                        "URL": None,
                    },
                ],
                "Err": {},
            },
        )
    )

    collector = CoinDeskNewsCollector(CoinDeskNewsClient(api_key=None))
    items = collector.collect(session, limit_per_feed=10)

    assert len(items) == 2
    persisted = session.query(NewsItem).order_by(NewsItem.url).all()
    assert {p.url for p in persisted} == {
        "https://example.com/1",
        "https://example.com/2",
    }
    by_url = {p.url: p for p in persisted}
    first = by_url["https://example.com/1"]
    assert first.sentiment == "POSITIVE"
    assert first.sentiment_score == 1.0
    assert by_url["https://example.com/2"].sentiment == "NEGATIVE"
    assert by_url["https://example.com/2"].sentiment_score == -1.0
    assert first.source == "CoinDesk"
    # SUBTITLE only — BODY discarded
    assert first.summary == "Short summary line."
    assert "all-time high" not in first.summary
    assert first.score == 7
    assert first.categories == ["BTC", "MARKET"]


@respx.mock
def test_collector_handles_http_errors_gracefully(session) -> None:
    respx.get("https://data-api.coindesk.com/news/v1/article/list").mock(
        return_value=httpx.Response(500, json={})
    )
    collector = CoinDeskNewsCollector(CoinDeskNewsClient(api_key=None))
    items = collector.collect(session)
    assert items == []
    assert session.query(NewsItem).count() == 0


@respx.mock
def test_collector_filters_already_seen_articles_by_published_ts(session) -> None:
    AppState.set(session, "coindesk_last_published_ts", "1714000050")
    respx.get("https://data-api.coindesk.com/news/v1/article/list").mock(
        return_value=httpx.Response(
            200,
            json={
                "Data": [
                    {
                        "ID": 1,
                        "TITLE": "old",
                        "URL": "https://example.com/old",
                        "PUBLISHED_ON": 1714000000,
                        "SENTIMENT": "POSITIVE",
                        "SOURCE_DATA": {"NAME": "X"},
                    },
                    {
                        "ID": 2,
                        "TITLE": "boundary",
                        "URL": "https://example.com/boundary",
                        "PUBLISHED_ON": 1714000050,
                        "SENTIMENT": "NEUTRAL",
                        "SOURCE_DATA": {"NAME": "X"},
                    },
                    {
                        "ID": 3,
                        "TITLE": "new",
                        "URL": "https://example.com/new",
                        "PUBLISHED_ON": 1714000100,
                        "SENTIMENT": "NEGATIVE",
                        "SOURCE_DATA": {"NAME": "X"},
                    },
                ],
                "Err": {},
            },
        )
    )

    collector = CoinDeskNewsCollector(CoinDeskNewsClient(api_key=None))
    items = collector.collect(session)

    assert {i.url for i in items} == {"https://example.com/new"}
    assert AppState.get(session, "coindesk_last_published_ts") == "1714000100"


@respx.mock
def test_collector_skips_when_err_payload_present(session) -> None:
    respx.get("https://data-api.coindesk.com/news/v1/article/list").mock(
        return_value=httpx.Response(
            200, json={"Data": [], "Err": {"message": "rate limit"}}
        )
    )
    collector = CoinDeskNewsCollector(CoinDeskNewsClient(api_key=None))
    assert collector.collect(session) == []
