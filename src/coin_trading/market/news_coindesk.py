"""CoinDesk Data API news collector.

Endpoint: GET https://data-api.coindesk.com/news/v1/article/list
Docs: https://developers.coindesk.com/documentation/data-api/news_v1_article_list
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from coin_trading.db.models import AppState, NewsItem

logger = logging.getLogger(__name__)


_LAST_FETCH_KEY = "coindesk_last_published_ts"


_SENTIMENT_MAP: dict[str, float] = {
    "POSITIVE": 1.0,
    "NEUTRAL": 0.0,
    "NEGATIVE": -1.0,
}


class CoinDeskNewsClient:
    BASE_URL = "https://data-api.coindesk.com/news/v1/article/list"

    def __init__(
        self,
        api_key: str | None = None,
        lang: str = "EN",
        categories: list[str] | None = None,
        exclude_categories: list[str] | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.api_key = api_key
        self.lang = lang
        self.categories = categories or []
        self.exclude_categories = exclude_categories or []
        self.timeout = timeout

    def fetch(self, limit: int = 50, to_ts: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"lang": self.lang, "limit": limit}
        if self.categories:
            params["categories"] = ",".join(self.categories)
        if self.exclude_categories:
            params["exclude_categories"] = ",".join(self.exclude_categories)
        if to_ts is not None:
            params["to_ts"] = to_ts

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Apikey {self.api_key}"

        with httpx.Client(timeout=self.timeout) as client:
            response = client.get(self.BASE_URL, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()

        err = payload.get("Err") or {}
        if err:
            logger.warning("CoinDesk API returned error payload: %s", err)
            return []
        return list(payload.get("Data") or [])


class CoinDeskNewsCollector:
    def __init__(self, client: CoinDeskNewsClient) -> None:
        self.client = client

    def collect(self, session: Session, limit_per_feed: int = 50) -> list[NewsItem]:
        last_ts = _get_last_fetch_ts(session)
        # to_ts: upper bound (now). Server returns latest <= to_ts.
        # Incremental filter happens client-side via published_on > last_ts.
        now_ts = int(datetime.now(timezone.utc).timestamp())
        try:
            articles = self.client.fetch(limit=limit_per_feed, to_ts=now_ts)
        except httpx.HTTPError as exc:
            logger.warning("CoinDesk fetch failed: %s", exc)
            return []

        items: list[NewsItem] = []
        max_seen = last_ts
        for article in articles:
            url = article.get("URL")
            if not url:
                continue
            published_on = article.get("PUBLISHED_ON")
            if isinstance(published_on, int) and published_on <= last_ts:
                continue
            # Track high watermark even when DB rejects (URL dedup) so we
            # don't re-process the same articles on the next cycle.
            if isinstance(published_on, int) and published_on > max_seen:
                max_seen = published_on
            sentiment_raw = article.get("SENTIMENT")
            item = NewsItem(
                title=(article.get("TITLE") or "")[:500],
                summary=(article.get("SUBTITLE") or "")[:1000],
                source=_extract_source(article),
                url=url,
                sentiment=_normalize_sentiment(sentiment_raw),
                sentiment_score=_map_sentiment(sentiment_raw),
                score=_safe_int(article.get("SCORE")),
                categories=_extract_categories(article),
                published_at=_parse_unix_ts(published_on),
            )
            session.add(item)
            try:
                session.flush()
                items.append(item)
            except IntegrityError:
                session.rollback()
        session.commit()

        if max_seen > last_ts:
            AppState.set(session, _LAST_FETCH_KEY, str(max_seen))

        logger.info(
            "[CoinDesk] fetched=%d new=%d last_ts=%d -> %d",
            len(articles), len(items), last_ts, max_seen,
        )
        return items

    @staticmethod
    def latest(session: Session, limit: int = 10) -> list[NewsItem]:
        return (
            session.query(NewsItem)
            .order_by(NewsItem.collected_at.desc())
            .limit(limit)
            .all()
        )


def _get_last_fetch_ts(session: Session) -> int:
    raw = AppState.get(session, _LAST_FETCH_KEY)
    if not raw:
        return 0
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _extract_categories(article: dict[str, Any]) -> list[str]:
    raw = article.get("CATEGORY_DATA") or []
    out: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("CATEGORY") or entry.get("NAME")
        if name:
            out.append(str(name))
    return out


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_source(article: dict[str, Any]) -> str:
    source_data = article.get("SOURCE_DATA") or {}
    name = source_data.get("NAME") or source_data.get("KEY")
    if name:
        return str(name)[:255]
    source_id = article.get("SOURCE_ID")
    return f"coindesk:{source_id}" if source_id else "coindesk"


def _map_sentiment(value: str | None) -> float | None:
    if not value:
        return None
    return _SENTIMENT_MAP.get(value.upper())


def _normalize_sentiment(value: str | None) -> str | None:
    if not value:
        return None
    upper = value.upper()
    return upper if upper in _SENTIMENT_MAP else None


def _parse_unix_ts(value: int | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None
