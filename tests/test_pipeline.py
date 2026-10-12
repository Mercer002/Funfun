"""End-to-end integration tests for the Multi-Factor Stock Scoring Pipeline."""

import json
from unittest.mock import MagicMock, patch
import pytest

from run_pipeline import setup_mock_environment
from src.ingestion_lambda import ScrapedPost
from src.lambda_handler import lambda_handler
from src.pipeline import PipelineRunner


class TestPipelineIntegration:
    def test_pipeline_end_to_end_mock(self):
        ingestion_svc, scoring_eng = setup_mock_environment()
        runner = PipelineRunner(
            ingestion_service=ingestion_svc,
            scoring_engine=scoring_eng
        )

        # Run pipeline in dry_run mode
        result = runner.run(dry_run=True)

        assert result["status"] == "success"
        assert result["flagged_count"] > 0
        assert result["scored_count"] > 0

        # Winners should have score > 75.0
        for winner in result["winners"]:
            assert winner["total_score"] > 75.0
            assert "math_breakdown" in winner
            assert len(winner["math_breakdown"]) > 0

        winner_tickers = [w["ticker"] for w in result["winners"]]
        # ACQR is a speculative microcap rescued by M&A or BIOC / NVDA
        assert "NVDA" in winner_tickers or "ACQR" in winner_tickers or "BIOC" in winner_tickers

    def test_pipeline_with_custom_posts(self):
        ingestion_svc, scoring_eng = setup_mock_environment()
        runner = PipelineRunner(
            ingestion_service=ingestion_svc,
            scoring_engine=scoring_eng
        )

        custom_posts = [
            ScrapedPost(
                title="Massive buyout offer: $ACQR to be acquired in all-cash takeover!",
                selftext="Deal valued at 100% premium to current share price. Merger expected in Q4.",
                subreddit="wallstreetbets",
                created_utc=100.0,
                post_id="post_acqr_1"
            ),
            ScrapedPost(
                title="ACQR options frenzy as acquisition rumors accelerate",
                selftext="Unusual call volume detected on ACQR.",
                subreddit="options",
                created_utc=101.0,
                post_id="post_acqr_2"
            ),
            ScrapedPost(
                title="Biotech $BIOC secures landmark FDA approval",
                selftext="Phase 3 primary endpoints met and FDA marketing authorization granted.",
                subreddit="stocks",
                created_utc=102.0,
                post_id="post_bioc_1"
            ),
        ]

        result = runner.run(custom_posts=custom_posts, dry_run=True)
        assert result["status"] == "success"
        assert result["flagged_count"] >= 1

    @patch("src.lambda_handler.PipelineRunner")
    def test_lambda_handler_execution(self, mock_runner_cls):
        mock_runner = MagicMock()
        mock_runner.run.return_value = {
            "status": "success",
            "flagged_count": 2,
            "scored_count": 2,
            "winners_count": 1,
            "winners": [
                {"ticker": "NVDA", "total_score": 120.0, "catalyst": "EARNINGS_BEAT"}
            ]
        }
        mock_runner_cls.return_value = mock_runner

        event = {"dry_run": True}
        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "success"
        assert body["winners_count"] == 1

