#!/usr/bin/env python3
"""
Local CLI Runner for the Multi-Factor Stock Scoring Engine v2.0.
Usage:
    python3 run_pipeline.py --mock --verbose
    python3 run_pipeline.py --dry-run
"""

import argparse
import json
import logging
import sys

from src.config import settings
from src.ingestion_lambda import DynamoDBStateStore, SocialIngestionService, ScrapedPost
from src.notifier import NotifierService
from src.pipeline import PipelineRunner
from src.scoring_engine import FundamentalDataProvider, FundamentalMetrics, MultiFactorScoringEngine
from src.sentiment_parser import SentimentParser


def setup_mock_environment():
    """Sets up in-memory mock stores and mock fundamental providers for local dry-runs."""
    class InMemoryDynamoDB:
        def __init__(self):
            # Preload some historical baselines
            self.store = {
                "NVDA": {"history": [0, 1, 0, 0, 0, 0, 0], "avg": 0.14},
                "ACQR": {"history": [0, 1, 0, 0, 0, 0, 0], "avg": 0.14},
                "BIOC": {"history": [0, 0, 0, 1, 0, 0, 0], "avg": 0.14},
                "TSLA": {"history": [12, 14, 15, 13, 16, 14, 15], "avg": 14.1},
                "FAIL": {"history": [2, 1, 3, 2, 2, 1, 2], "avg": 1.8},
            }

        def get_ticker_history(self, ticker: str):
            if ticker in self.store:
                return self.store[ticker]["history"], self.store[ticker]["avg"]
            return [], 0.0

        def update_ticker_state(self, ticker: str, today_count: int, max_days: int = 7):
            history, old_avg = self.get_ticker_history(ticker)
            new_history = (history + [today_count])[-max_days:]
            new_avg = sum(new_history) / len(new_history)
            self.store[ticker] = {"history": new_history, "avg": new_avg}
            return old_avg, new_avg

    mock_db = InMemoryDynamoDB()

    # Synthetic financial data reflecting diverse market profiles:
    # 1. ACQR: Unprofitable target (-15 fundamental penalty) offset by M&A rumor (+50 pts) -> 95 pts (Rescued!)
    # 2. BIOC: Clinical-stage micro-cap (-35 fundamental penalty) offset by FDA approval (+40 pts)
    # 3. NVDA: Profitable mega-cap (+40 fundamental max) with earnings beat (+35 pts) -> 135 pts
    # 4. TSLA: Large cap with neutral/bearish sentiment
    # 5. FAIL: Penny stock with bankruptcy risk
    mock_fundamentals = {
        "ACQR": FundamentalMetrics(
            market_cap=650_000_000,          # Small-cap $500M-$2B (+5 pts)
            operating_cash_flow=-18_000_000, # Negative Cash Flow (-20 pts)
            forward_pe=None,                 # Unprofitable (0 pts) -> Subtotal = -15 pts
            company_name="Acquiree Biotech Corp"
        ),
        "BIOC": FundamentalMetrics(
            market_cap=320_000_000,          # Micro-cap < $500M (-15 pts)
            operating_cash_flow=-25_000_000, # Negative Cash Flow (-20 pts)
            forward_pe=None,                 # Unprofitable (0 pts)
            company_name="BioCure Therapeutics"
        ),
        "NVDA": FundamentalMetrics(
            market_cap=3_100_000_000_000,    # Mega-cap > $2B (+15 pts)
            operating_cash_flow=45_000_000_000, # Positive Cash Flow (+15 pts)
            forward_pe=24.5,                 # Forward P/E < 25 (+10 pts)
            company_name="NVIDIA Corporation"
        ),
        "TSLA": FundamentalMetrics(
            market_cap=750_000_000_000,      # Large-cap > $2B (+15 pts)
            operating_cash_flow=12_000_000_000, # Positive Cash Flow (+15 pts)
            forward_pe=68.0,                 # High P/E > 50 (0 pts)
            company_name="Tesla, Inc."
        ),
        "FAIL": FundamentalMetrics(
            market_cap=15_000_000,           # Micro-cap < $500M (-15 pts)
            operating_cash_flow=-40_000_000, # Negative Cash Flow (-20 pts)
            forward_pe=None,
            company_name="Failing Ventures Inc"
        ),
    }

    class MockIngestionService(SocialIngestionService):
        def __init__(self):
            super().__init__()
            self.state_store = mock_db

        def fetch_reddit_posts(self, limit=None):
            return self._fetch_fallback_posts(limit or 200)

    provider = FundamentalDataProvider(mock_data=mock_fundamentals)
    scoring_engine = MultiFactorScoringEngine(data_provider=provider)

    return MockIngestionService(), scoring_engine


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Factor Stock Scoring Engine v2.0 Local Runner"
    )
    parser.add_argument("--mock", action="store_true", help="Run with synthetic test feeds and mock data")
    parser.add_argument("--dry-run", action="store_true", help="Do not dispatch external Discord/SNS webhooks")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose debug logging")
    parser.add_argument("--threshold", type=float, default=75.0, help="Custom minimum total score threshold")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

    settings.total_score_threshold = args.threshold

    print("=" * 70)
    print("  MULTI-FACTOR STOCK SCORING ENGINE v2.0")
    print(f"  Mode: {'MOCK & SYNTHETIC DATA' if args.mock else 'LIVE DATA SOURCES'}")
    print(f"  Dry-Run Webhooks: {args.dry_run}")
    print(f"  Score Threshold: > {args.threshold} pts")
    print("=" * 70)

    if args.mock:
        ingestion_svc, scoring_eng = setup_mock_environment()
        runner = PipelineRunner(
            ingestion_service=ingestion_svc,
            scoring_engine=scoring_eng
        )
    else:
        runner = PipelineRunner()

    result = runner.run(dry_run=args.dry_run)

    print("\n" + "=" * 70)
    print("  PIPELINE EXECUTION SUMMARY")
    print("=" * 70)
    print(f"  • Tickers Passing Velocity Spike Filter (>150%): {result['flagged_count']}")
    print(f"  • Total Evaluated & Scored:                    {result['scored_count']}")
    print(f"  • Winners Qualified (> {args.threshold} pts):              {result['winners_count']}")
    print("=" * 70)

    if result["winners"]:
        print("\n🏆 WINNING CANDIDATES QUALIFYING FOR ALERT ROUTING:")
        for idx, winner in enumerate(result["winners"], 1):
            print(f"\n[{idx}] ${winner['ticker']} — Score: {winner['total_score']} pts | Catalyst: {winner['catalyst']}")
            if winner.get("is_rescued_by_catalyst"):
                print("    ⭐ CATALYST RESCUE: Overcame negative fundamental penalty!")
            print("\n" + winner["math_breakdown"] + "\n")
    else:
        print("\nNo stocks crossed the >75.0 points threshold.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
