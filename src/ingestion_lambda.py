"""
Module 1: Ingestion & DynamoDB State
Scrapes social posts, extracts stock tickers via regex, computes 7-day rolling velocity,
updates DynamoDB, and soft-filters tickers with >150% volume spikes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

import boto3
from botocore.exceptions import ClientError

from src.config import Settings, settings

logger = logging.getLogger(__name__)


@dataclass
class FlaggedTicker:
    """Represents a ticker that passed the velocity spike threshold."""
    ticker: str
    today_mentions: int
    seven_day_avg: float
    velocity_spike_pct: float
    aggregated_text: str = ""
    post_count: int = 0


@dataclass
class ScrapedPost:
    """Represents a post retrieved from Reddit or alternative feeds."""
    title: str
    selftext: str
    subreddit: str
    created_utc: float
    post_id: str


class TickerExtractor:
    """Extracts stock tickers using regular expressions and false-positive filtering."""

    # Matches cashtags like $AAPL or $TSLA
    CASHTAG_PATTERN = re.compile(r"\$([A-Z]{1,5})\b")

    # Matches standalone uppercase words of length 2-5
    STANDALONE_PATTERN = re.compile(r"\b([A-Z]{2,5})\b")

    def __init__(self, blacklist: Optional[Set[str]] = None):
        self.blacklist = blacklist if blacklist is not None else settings.ticker_blacklist

    def extract_tickers(self, text: str) -> Set[str]:
        """Extract valid tickers from a body of text."""
        if not text:
            return set()

        tickers = set()

        # 1. First priority: explicit cashtags ($TSLA, $AAPL)
        for match in self.CASHTAG_PATTERN.finditer(text):
            candidate = match.group(1).upper()
            if candidate not in self.blacklist:
                tickers.add(candidate)

        # 2. Standalone uppercase ticker symbols
        for match in self.STANDALONE_PATTERN.finditer(text):
            candidate = match.group(1).upper()
            if candidate not in self.blacklist and len(candidate) >= 2:
                tickers.add(candidate)

        return tickers


class DynamoDBStateStore:
    """Manages 7-day rolling mention history and velocity in DynamoDB."""

    def __init__(
        self,
        table_name: Optional[str] = None,
        region_name: Optional[str] = None,
        endpoint_url: Optional[str] = None
    ):
        self.table_name = table_name or settings.stock_mentions_table
        self.region_name = region_name or settings.aws_region
        self.endpoint_url = endpoint_url or settings.dynamodb_endpoint_url or None

        self._dynamodb = None
        self._table = None
        self._local_cache_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".stock_mentions_cache.json")
        self._local_cache: Optional[Dict[str, Any]] = None

    def _get_local_cache(self) -> Dict[str, Any]:
        if self._local_cache is None:
            if os.path.exists(self._local_cache_file):
                try:
                    with open(self._local_cache_file, "r") as f:
                        self._local_cache = json.load(f)
                except Exception:
                    self._local_cache = {}
            else:
                self._local_cache = {}
        return self._local_cache

    def _save_local_cache(self):
        if self._local_cache is not None:
            try:
                with open(self._local_cache_file, "w") as f:
                    json.dump(self._local_cache, f, indent=2)
            except Exception as e:
                logger.debug("Could not save local cache: %s", e)

    @property
    def table(self):
        if self._table is None:
            kwargs: Dict[str, Any] = {"region_name": self.region_name}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            self._dynamodb = boto3.resource("dynamodb", **kwargs)
            self._table = self._dynamodb.Table(self.table_name)
        return self._table

    def get_ticker_history(self, ticker: str) -> Tuple[List[int], float]:
        """
        Retrieves the 7-day mention history and computed rolling average for a ticker.
        Falls back to local file state if AWS DynamoDB credentials are not present.
        """
        try:
            response = self.table.get_item(Key={"ticker": ticker})
            item = response.get("Item")
            if not item:
                return [], 0.0

            raw_history = item.get("history_mentions", [])
            history = [int(x) for x in raw_history]
            avg = float(item.get("seven_day_avg", 0.0))
            if history and avg == 0.0:
                avg = sum(history) / len(history)
            return history, avg
        except (ClientError, Exception) as exc:
            # Fallback to local cache when offline or AWS credentials missing
            cache = self._get_local_cache()
            if ticker in cache:
                hist = [int(x) for x in cache[ticker].get("history", [])]
                avg = float(cache[ticker].get("avg", 0.0))
                return hist, avg
            return [], 0.0

    def update_ticker_state(
        self, ticker: str, today_count: int, max_days: int = 7
    ) -> Tuple[float, float]:
        """
        Appends today's count to the rolling history (capped at max_days),
        calculates new rolling average and returns (old_avg, new_avg).
        """
        history, old_avg = self.get_ticker_history(ticker)

        # Append today's count and keep the most recent max_days
        updated_history = (history + [today_count])[-max_days:]
        new_avg = sum(updated_history) / len(updated_history) if updated_history else 0.0

        now_dt = datetime.now(timezone.utc)
        # 90-day time-to-live expiration timestamp
        ttl_timestamp = int(now_dt.timestamp()) + (90 * 86400)

        # Update local file cache fallback
        cache = self._get_local_cache()
        cache[ticker] = {"history": updated_history, "avg": round(new_avg, 2)}
        self._save_local_cache()

        try:
            self.table.put_item(
                Item={
                    "ticker": ticker,
                    "history_mentions": [Decimal(str(x)) for x in updated_history],
                    "seven_day_avg": Decimal(str(round(new_avg, 2))),
                    "last_updated": now_dt.isoformat(),
                    "today_mentions": Decimal(str(today_count)),
                    "ttl": ttl_timestamp
                }
            )
        except (ClientError, Exception):
            pass

        return old_avg, new_avg


def calculate_velocity_spike(today_mentions: int, baseline_avg: float) -> float:
    """
    Calculates percentage spike in today's volume relative to the 7-day average.
    Formula: ((today - baseline) / baseline) * 100.
    Handles zero-baseline gracefully: if baseline is 0 and mentions >= 5, returns 200.0%.
    """
    if baseline_avg <= 0:
        if today_mentions >= 5:
            # Significant initial breakout from zero history
            return 200.0
        return 0.0

    spike_pct = ((today_mentions - baseline_avg) / baseline_avg) * 100.0
    return round(spike_pct, 2)


class SocialIngestionService:
    """Scrapes Reddit or fallback feeds and aggregates ticker mentions."""

    def __init__(
        self,
        config: Optional[Settings] = None,
        extractor: Optional[TickerExtractor] = None,
        state_store: Optional[DynamoDBStateStore] = None
    ):
        self.config = config or settings
        self.extractor = extractor or TickerExtractor(self.config.ticker_blacklist)
        self.state_store = state_store or DynamoDBStateStore(
            table_name=self.config.stock_mentions_table,
            region_name=self.config.aws_region,
            endpoint_url=self.config.dynamodb_endpoint_url
        )

    def fetch_reddit_posts(self, limit: Optional[int] = None) -> List[ScrapedPost]:
        """Fetches posts via PRAW if credentials exist, else falls back to RSS/mock."""
        limit = limit or self.config.post_limit

        if self.config.reddit_client_id and self.config.reddit_client_secret:
            try:
                import praw  # type: ignore

                reddit = praw.Reddit(
                    client_id=self.config.reddit_client_id,
                    client_secret=self.config.reddit_client_secret,
                    user_agent=self.config.reddit_user_agent
                )
                sub_str = "+".join(self.config.subreddits)
                subreddit = reddit.subreddit(sub_str)

                posts: List[ScrapedPost] = []
                for p in subreddit.new(limit=limit):
                    posts.append(
                        ScrapedPost(
                            title=p.title or "",
                            selftext=p.selftext or "",
                            subreddit=str(p.subreddit),
                            created_utc=float(p.created_utc),
                            post_id=str(p.id)
                        )
                    )
                logger.info("Successfully fetched %d posts via PRAW", len(posts))
                return posts
            except Exception as exc:
                logger.warning("PRAW ingestion failed (%s), trying public feeds", exc)

        # Attempt live RSS feed ingestion from public financial feeds
        rss_posts = self._fetch_rss_posts(limit)
        if rss_posts:
            logger.info("Successfully fetched %d live posts from public financial RSS feeds", len(rss_posts))
            return rss_posts

        return self._fetch_fallback_posts(limit)

    def _fetch_rss_posts(self, limit: int) -> List[ScrapedPost]:
        """Scrapes live posts from public financial RSS feeds when PRAW credentials are not set."""
        import xml.etree.ElementTree as ET
        import requests

        feed_urls = [
            ("r/stocks", "https://www.reddit.com/r/stocks/.rss"),
            ("yahoo_finance", "https://finance.yahoo.com/news/rssindex"),
            ("google_finance", "https://news.google.com/rss/search?q=stocks+market+earnings+buyout")
        ]
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        collected: List[ScrapedPost] = []

        for source_name, url in feed_urls:
            try:
                resp = requests.get(url, headers=headers, timeout=4)
                if resp.status_code == 200:
                    root = ET.fromstring(resp.content)
                    # Support both standard RSS (<item>) and Atom (<entry>)
                    items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
                    for idx, item in enumerate(items):
                        if len(collected) >= limit:
                            break
                        title_el = item.find("title")
                        if title_el is None:
                            title_el = item.find("{http://www.w3.org/2005/Atom}title")

                        desc_el = item.find("description")
                        if desc_el is None:
                            desc_el = item.find("{http://www.w3.org/2005/Atom}content")
                        if desc_el is None:
                            desc_el = item.find("{http://www.w3.org/2005/Atom}summary")

                        title_text = title_el.text if title_el is not None and title_el.text else ""
                        desc_text = desc_el.text if desc_el is not None and desc_el.text else ""
                        # Strip basic HTML tags from descriptions
                        clean_desc = re.sub(r"<[^>]+>", " ", desc_text).strip()

                        if title_text:
                            collected.append(
                                ScrapedPost(
                                    title=title_text,
                                    selftext=clean_desc[:500],
                                    subreddit=source_name,
                                    created_utc=datetime.now(timezone.utc).timestamp(),
                                    post_id=f"rss_{source_name}_{idx}"
                                )
                            )
            except Exception as exc:
                logger.debug("Failed fetching RSS feed %s: %s", url, exc)

        return collected[:limit]

    def _fetch_fallback_posts(self, limit: int) -> List[ScrapedPost]:
        """Provides simulated or fallback posts for testing/offline execution."""
        sample_posts = [
            ScrapedPost(
                title="$BIOC Biotech receives FDA approval for breakthrough oncology treatment!",
                selftext="BIOC announced Phase 3 results and subsequent FDA approval today. Unbelievable momentum.",
                subreddit="wallstreetbets",
                created_utc=datetime.now(timezone.utc).timestamp(),
                post_id="sample_bioc_1"
            ),
            ScrapedPost(
                title="Rumors of Tech Giant in talks to acquire $ACQR for $1.8B buyout",
                selftext="Multiple sources confirm M&A discussions for ACQR. Acquisition premium could be 80%.",
                subreddit="stocks",
                created_utc=datetime.now(timezone.utc).timestamp(),
                post_id="sample_acqr_1"
            ),
            ScrapedPost(
                title="Massive call volume on ACQR ahead of expected merger announcement",
                selftext="Unusual options activity on ACQR strikes, heavy whale buying.",
                subreddit="options",
                created_utc=datetime.now(timezone.utc).timestamp(),
                post_id="sample_acqr_2"
            ),
            ScrapedPost(
                title="$NVDA crushes Q3 earnings beat and raises forward guidance by 40%",
                selftext="Data center revenue surges, Jensen comments on massive demand for Blackwell GPUs.",
                subreddit="wallstreetbets",
                created_utc=datetime.now(timezone.utc).timestamp(),
                post_id="sample_nvda_1"
            ),
            ScrapedPost(
                title="Why NVDA remains unstoppable: Earnings beat and guidance raise breakdown",
                selftext="Fundamental analysis of today's stellar report and cash flow trajectory.",
                subreddit="stocks",
                created_utc=datetime.now(timezone.utc).timestamp(),
                post_id="sample_nvda_2"
            ),
            ScrapedPost(
                title="TSLA vehicle delivery delays and margin compression worries",
                selftext="Price cuts continue to weigh down margins, sentiment turning cautious on TSLA.",
                subreddit="stocks",
                created_utc=datetime.now(timezone.utc).timestamp(),
                post_id="sample_tsla_1"
            ),
            ScrapedPost(
                title="Penny stock $FAIL files for Chapter 11 bankruptcy restructuring",
                selftext="Zero cash left, reverse split failed, completely uninvestable.",
                subreddit="wallstreetbets",
                created_utc=datetime.now(timezone.utc).timestamp(),
                post_id="sample_fail_1"
            ),
        ]
        return sample_posts[:limit]

    def process_and_filter(
        self, posts: Optional[List[ScrapedPost]] = None
    ) -> List[FlaggedTicker]:
        """
        Extracts tickers from posts, computes velocity using DynamoDB state,
        updates rolling state, and returns tickers passing the >150% soft filter.
        """
        if posts is None:
            posts = self.fetch_reddit_posts()

        ticker_mentions: Dict[str, int] = {}
        ticker_texts: Dict[str, List[str]] = {}

        # 1. Extract tickers and aggregate text snippets
        for post in posts:
            full_text = f"{post.title} {post.selftext}"
            extracted = self.extractor.extract_tickers(full_text)

            for ticker in extracted:
                ticker_mentions[ticker] = ticker_mentions.get(ticker, 0) + 1
                if ticker not in ticker_texts:
                    ticker_texts[ticker] = []
                ticker_texts[ticker].append(f"[{post.subreddit}] {post.title}: {post.selftext[:200]}")

        # 2. Check velocity spikes via DynamoDB rolling state
        flagged: List[FlaggedTicker] = []
        spike_threshold = self.config.spike_threshold_percent

        for ticker, today_count in ticker_mentions.items():
            old_avg, _ = self.state_store.update_ticker_state(ticker, today_count)
            spike_pct = calculate_velocity_spike(today_count, old_avg)

            # Soft Filter: passes any ticker with > 150% volume spike
            if spike_pct > spike_threshold:
                aggregated = "\n".join(ticker_texts.get(ticker, []))
                flagged.append(
                    FlaggedTicker(
                        ticker=ticker,
                        today_mentions=today_count,
                        seven_day_avg=old_avg,
                        velocity_spike_pct=spike_pct,
                        aggregated_text=aggregated,
                        post_count=len(ticker_texts.get(ticker, []))
                    )
                )

        # Sort flagged tickers by velocity spike descending
        flagged.sort(key=lambda x: x.velocity_spike_pct, reverse=True)
        return flagged


def handler(event: Any, context: Any) -> Dict[str, Any]:
    """AWS Lambda entry point for Module 1."""
    service = SocialIngestionService()
    flagged = service.process_and_filter()

    return {
        "statusCode": 200,
        "flagged_tickers": [
            {
                "ticker": f.ticker,
                "today_mentions": f.today_mentions,
                "seven_day_avg": f.seven_day_avg,
                "velocity_spike_pct": f.velocity_spike_pct,
                "post_count": f.post_count,
                "aggregated_text": f.aggregated_text
            }
            for f in flagged
        ]
    }

