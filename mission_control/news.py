"""Yahoo Finance RSS news adapter with bounded requests and stale fallback.

One combined ticker feed is fetched at most once per ten-minute cache window.
This module does not call TradingView or any backup news provider.
"""
from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

YAHOO_FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
YAHOO_CALENDAR_URL = "https://finance.yahoo.com/calendar/economic/"
NEWS_SYMBOLS = ("MNQ=F", "MES=F", "MGC=F", "QQQ", "SPY", "GLD")
CACHE_TTL_SECONDS = 600.0
ERROR_COOLDOWN_SECONDS = 900.0
MAX_ITEMS = 30


class YahooNewsUnavailable(RuntimeError):
    """Yahoo Finance news could not be refreshed."""


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def fetch_yahoo_rss() -> str:
    """Fetch one combined Yahoo Finance ticker RSS feed."""
    query = urlencode(
        {
            "s": ",".join(NEWS_SYMBOLS),
            "region": "US",
            "lang": "en-US",
        }
    )
    request = Request(
        f"{YAHOO_FEED_URL}?{query}",
        headers={"User-Agent": "Mozilla/5.0 FAB787-QUANTUM/0.3"},
    )
    with urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8", errors="replace")


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    return parser.text()


def parse_yahoo_rss(xml_text: str, *, received_at: datetime | None = None) -> list[dict[str, Any]]:
    """Parse Yahoo RSS items without assuming every optional field exists."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise YahooNewsUnavailable("Yahoo Finance returned invalid RSS/XML") from exc

    received = received_at or datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in root.findall(".//item"):
        title = _clean_text(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        if urlsplit(link).scheme != "https" or not urlsplit(link).netloc:
            continue
        guid = (item.findtext("guid") or link or title).strip()
        if not title or not link or guid in seen:
            continue
        seen.add(guid)
        raw_date = item.findtext("pubDate")
        published_at = None
        if raw_date:
            try:
                parsed = parsedate_to_datetime(raw_date)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                published_at = parsed.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                published_at = None
        source = item.findtext("source")
        categories = [
            _clean_text(node.text)
            for node in item.findall("category")
            if node.text and _clean_text(node.text)
        ]
        items.append(
            {
                "id": guid,
                "title": title,
                "summary": _clean_text(item.findtext("description")),
                "url": link,
                "publisher": _clean_text(source) or "Yahoo Finance",
                "published_at": published_at,
                "received_at": received.astimezone(timezone.utc).isoformat(),
                "categories": categories,
            }
        )
        if len(items) >= MAX_ITEMS:
            break
    return items


class YahooFinanceNewsService:
    """Single-flight, ten-minute cache and fifteen-minute failure cooldown."""

    def __init__(
        self,
        *,
        fetcher: Callable[[], str] = fetch_yahoo_rss,
        clock: Callable[[], float] = time.monotonic,
        ttl_seconds: float = CACHE_TTL_SECONDS,
        error_cooldown_seconds: float = ERROR_COOLDOWN_SECONDS,
    ) -> None:
        self._fetcher = fetcher
        self._clock = clock
        self.ttl_seconds = ttl_seconds
        self.error_cooldown_seconds = error_cooldown_seconds
        self._cache: tuple[float, dict[str, Any]] | None = None
        self._lock = asyncio.Lock()
        self._cooldown_until = 0.0
        self.fetch_count = 0

    def _cached_response(self, status: str, now: float) -> dict[str, Any]:
        assert self._cache is not None
        stored_at, payload = self._cache
        response = {
            **payload,
            "status": status,
            "upstream_requests": self.fetch_count,
            "provider_requests_per_refresh": 1,
            "cache_age_seconds": round(max(0.0, now - stored_at), 1),
            "next_refresh_seconds": round(max(0.0, stored_at + self.ttl_seconds - now), 1),
        }
        if status == "stale" and now < self._cooldown_until:
            response["retry_after_seconds"] = round(self._cooldown_until - now, 1)
        return response

    def peek_news(self) -> dict[str, Any]:
        """Read the existing Yahoo cache without triggering a fetch or cooldown retry."""
        now = self._clock()
        if self._cache is None:
            return {
                "status": "not_loaded",
                "source": "Yahoo Finance RSS",
                "items": [],
                "upstream_requests": self.fetch_count,
                "provider_requests_triggered": 0,
                "message": "No Yahoo headlines have been loaded into the local cache yet.",
            }
        stored_at, payload = self._cache
        age = max(0.0, now - stored_at)
        status = "cached" if age < self.ttl_seconds else "stale"
        response = {
            **payload,
            "status": status,
            "upstream_requests": self.fetch_count,
            "provider_requests_per_refresh": 1,
            "cache_age_seconds": round(age, 1),
            "next_refresh_seconds": round(max(0.0, self.ttl_seconds - age), 1),
            "provider_requests_triggered": 0,
        }
        if status == "stale" and now < self._cooldown_until:
            response["retry_after_seconds"] = round(self._cooldown_until - now, 1)
        return response

    async def get_news(self) -> dict[str, Any]:
        now = self._clock()
        if self._cache and now - self._cache[0] < self.ttl_seconds:
            return self._cached_response("cached", now)
        if now < self._cooldown_until:
            if self._cache:
                return self._cached_response("stale", now)
            return {
                "status": "cooldown",
                "source": "Yahoo Finance RSS",
                "items": [],
                "upstream_requests": self.fetch_count,
                "provider_requests_per_refresh": 1,
                "error": "Yahoo Finance refresh is cooling down after a provider error.",
                "retry_after_seconds": round(self._cooldown_until - now, 1),
            }

        async with self._lock:
            now = self._clock()
            if self._cache and now - self._cache[0] < self.ttl_seconds:
                return self._cached_response("cached", now)
            if now < self._cooldown_until:
                if self._cache:
                    return self._cached_response("stale", now)
                return {
                    "status": "cooldown",
                    "source": "Yahoo Finance RSS",
                    "items": [],
                    "upstream_requests": self.fetch_count,
                    "provider_requests_per_refresh": 1,
                    "error": "Yahoo Finance refresh is cooling down after a provider error.",
                    "retry_after_seconds": round(self._cooldown_until - now, 1),
                }

            self.fetch_count += 1
            try:
                xml_text = await asyncio.to_thread(self._fetcher)
                items = parse_yahoo_rss(xml_text)
                fetched_at = datetime.now(timezone.utc).isoformat()
                payload = {
                    "source": "Yahoo Finance RSS",
                    "feed_url": YAHOO_FEED_URL,
                    "fetched_at": fetched_at,
                    "received_at": fetched_at,
                    "symbols": list(NEWS_SYMBOLS),
                    "items": items,
                    "item_count": len(items),
                    "message": None,
                }
            except Exception as exc:
                self._cooldown_until = self._clock() + self.error_cooldown_seconds
                if self._cache:
                    return self._cached_response("stale", self._clock())
                return {
                    "status": "unavailable",
                    "source": "Yahoo Finance RSS",
                    "items": [],
                    "upstream_requests": self.fetch_count,
                    "provider_requests_per_refresh": 1,
                    "error": f"Yahoo Finance news refresh failed: {type(exc).__name__}",
                    "retry_after_seconds": self.error_cooldown_seconds,
                }

            self._cache = (self._clock(), payload)
            return self._cached_response("fresh", self._clock())


yahoo_news_service = YahooFinanceNewsService()
