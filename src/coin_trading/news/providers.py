from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from coin_trading.db.models import NewsItem


class NewsCollector:
    def __init__(self, rss_urls: list[str]) -> None:
        self.rss_urls = rss_urls

    def collect(self, session: Session, limit_per_feed: int = 10) -> list[NewsItem]:
        items: list[NewsItem] = []
        for rss_url in self.rss_urls:
            feed = feedparser.parse(rss_url)
            source = feed.feed.get("title", rss_url)
            for entry in feed.entries[:limit_per_feed]:
                item = NewsItem(
                    title=entry.get("title", ""),
                    summary=entry.get("summary", ""),
                    source=source,
                    url=entry.get("link", f"{rss_url}#{entry.get('id', entry.get('title', 'unknown'))}"),
                    sentiment_score=self._simple_sentiment(entry.get("title", "")),
                    published_at=self._parse_published(entry.get("published")),
                )
                session.add(item)
                try:
                    session.flush()
                    items.append(item)
                except IntegrityError:
                    session.rollback()
        session.commit()
        return items

    @staticmethod
    def latest(session: Session, limit: int = 10) -> list[NewsItem]:
        return session.query(NewsItem).order_by(NewsItem.collected_at.desc()).limit(limit).all()

    @staticmethod
    def _parse_published(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _simple_sentiment(text: str) -> float:
        lowered = text.lower()
        positive = sum(word in lowered for word in ["bull", "surge", "rally", "etf", "gain"])
        negative = sum(word in lowered for word in ["bear", "crash", "hack", "lawsuit", "fall"])
        return float(max(-1, min(1, (positive - negative) / 3)))
