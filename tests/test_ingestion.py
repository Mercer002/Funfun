"""Tests for Module 1: Ingestion, Ticker Regex Extraction, and DynamoDB Velocity State."""

from decimal import Decimal
import pytest

from src.ingestion_lambda import (
    DynamoDBStateStore,
    FlaggedTicker,
    ScrapedPost,
    SocialIngestionService,
    TickerExtractor,
    calculate_velocity_spike,
)


class TestTickerExtractor:
    def test_cashtag_extraction(self):
        extractor = TickerExtractor()
        text = "Huge call sweep on $AAPL and $TSLA today! Also check $NVDA."
        tickers = extractor.extract_tickers(text)
        assert "AAPL" in tickers
        assert "TSLA" in tickers
        assert "NVDA" in tickers

    def test_standalone_ticker_extraction(self):
        extractor = TickerExtractor()
        text = "PLTR and MSFT are looking primed for breakout."
        tickers = extractor.extract_tickers(text)
        assert "PLTR" in tickers
        assert "MSFT" in tickers

    def test_blacklist_filtering(self):
        extractor = TickerExtractor()
        text = "I did DD on AI and YOLO calls. The CEO and CFO had ATH gains with SPY and QQQ."
        tickers = extractor.extract_tickers(text)
        for blacklisted in ["DD", "AI", "YOLO", "CEO", "CFO", "ATH", "SPY", "QQQ", "I", "THE"]:
            assert blacklisted not in tickers

    def test_empty_or_none_text(self):
        extractor = TickerExtractor()
        assert extractor.extract_tickers("") == set()
        assert extractor.extract_tickers(None) == set()


class TestVelocityCalculation:
    def test_positive_spike(self):
        # 10 today vs baseline 2.0 -> (10 - 2)/2 * 100 = 400.0%
        spike = calculate_velocity_spike(10, 2.0)
        assert spike == 400.0

    def test_moderate_spike(self):
        # 5 today vs baseline 2.0 -> (5 - 2)/2 * 100 = 150.0%
        spike = calculate_velocity_spike(5, 2.0)
        assert spike == 150.0

    def test_negative_spike(self):
        # 1 today vs baseline 5.0 -> (1 - 5)/5 * 100 = -80.0%
        spike = calculate_velocity_spike(1, 5.0)
        assert spike == -80.0

    def test_zero_baseline_breakout(self):
        # 6 today vs baseline 0.0 -> breakout rule >= 5 mentions yields 200.0%
        spike = calculate_velocity_spike(6, 0.0)
        assert spike == 200.0

    def test_zero_baseline_low_mentions(self):
        # 2 today vs baseline 0.0 -> < 5 mentions yields 0.0%
        spike = calculate_velocity_spike(2, 0.0)
        assert spike == 0.0


class MockDynamoDBStore:
    def __init__(self):
        self.store = {}

    def get_ticker_history(self, ticker: str):
        if ticker in self.store:
            hist = self.store[ticker]["history"]
            avg = sum(hist) / len(hist) if hist else 0.0
            return hist, avg
        return [], 0.0

    def update_ticker_state(self, ticker: str, today_count: int, max_days: int = 7):
        hist, old_avg = self.get_ticker_history(ticker)
        new_hist = (hist + [today_count])[-max_days:]
        new_avg = sum(new_hist) / len(new_hist)
        self.store[ticker] = {"history": new_hist, "avg": new_avg}
        return old_avg, new_avg


class TestSocialIngestionService:
    def test_process_and_filter_spike_threshold(self):
        mock_db = MockDynamoDBStore()
        # Seed NVDA with baseline avg of 2.0 (so 8 mentions will be 300% spike)
        mock_db.store["NVDA"] = {"history": [2, 2, 2, 2, 2, 2, 2], "avg": 2.0}
        # Seed TSLA with high baseline avg of 10.0 (so 10 mentions will be 0% spike)
        mock_db.store["TSLA"] = {"history": [10, 10, 10, 10, 10, 10, 10], "avg": 10.0}

        posts = [
            ScrapedPost(title="$NVDA is rallying!", selftext="Huge demand for NVDA", subreddit="stocks", created_utc=1.0, post_id="1"),
            ScrapedPost(title="NVDA chip record", selftext="NVDA leads again", subreddit="wallstreetbets", created_utc=2.0, post_id="2"),
            ScrapedPost(title="NVDA calls printing", selftext="NVDA $150 strike", subreddit="options", created_utc=3.0, post_id="3"),
            ScrapedPost(title="Bullish on NVDA", selftext="NVDA data center growth", subreddit="stocks", created_utc=4.0, post_id="4"),
            ScrapedPost(title="TSLA vehicle update", selftext="TSLA deliveries flat", subreddit="stocks", created_utc=5.0, post_id="5"),
        ]

        service = SocialIngestionService(state_store=mock_db)
        flagged = service.process_and_filter(posts)

        # NVDA was mentioned across 4 posts, giving today count 4 vs baseline 2.0 -> ((4-2)/2)*100 = 100%
        # Let's verify that when count is high enough to pass >150%, it's returned
        mock_db.store["NVDA"] = {"history": [1, 1, 1, 1, 1, 1, 1], "avg": 1.0}
        flagged = service.process_and_filter(posts)

        flagged_tickers = [f.ticker for f in flagged]
        assert "NVDA" in flagged_tickers
        assert "TSLA" not in flagged_tickers

