"""Tests for Module 4: Output & Routing."""

import json
from unittest.mock import MagicMock, patch
import pytest

from src.notifier import NotifierService
from src.scoring_engine import (
    FundamentalMetrics,
    MultiFactorScoringEngine,
    ScoreBreakdown,
    ScoredStock,
)


def create_dummy_scored_stock(ticker: str, score: float, is_rescued: bool = False) -> ScoredStock:
    breakdown = ScoreBreakdown(
        market_cap_pts=15,
        profitability_pts=15,
        valuation_pts=10,
        fundamental_subtotal=40,
        velocity_pts=30,
        sentiment_pts=30,
        sentiment_subtotal=60,
        catalyst_bonus_pts=35,
        catalyst_type="EARNINGS_BEAT",
        total_score=score,
        breakdown_text=f"Math breakdown for {ticker} = {score}",
        is_rescued_by_catalyst=is_rescued
    )
    return ScoredStock(
        ticker=ticker,
        total_score=score,
        fundamental_score=40,
        sentiment_score_points=60,
        catalyst_bonus=35,
        sentiment_polarity=0.9,
        velocity_spike_pct=550.0,
        catalyst="EARNINGS_BEAT",
        catalyst_reasoning="Earnings crushed consensus.",
        fundamentals=FundamentalMetrics(market_cap=10_000_000_000, forward_pe=20.0, company_name=f"{ticker} Inc"),
        breakdown=breakdown
    )


class TestNotifierService:
    def test_filter_winning_stocks_threshold(self):
        notifier = NotifierService()
        stocks = [
            create_dummy_scored_stock("LOW", 60.0),
            create_dummy_scored_stock("EXACT_EDGE", 75.0),
            create_dummy_scored_stock("QUALIFIED_A", 75.1),
            create_dummy_scored_stock("SUPER_WINNER", 120.0),
            create_dummy_scored_stock("QUALIFIED_B", 90.0),
        ]

        winners = notifier.filter_winning_stocks(stocks, threshold=75.0)

        # Only strictly > 75.0 qualify
        winner_tickers = [w.ticker for w in winners]
        assert "LOW" not in winner_tickers
        assert "EXACT_EDGE" not in winner_tickers
        assert "QUALIFIED_A" in winner_tickers
        assert "SUPER_WINNER" in winner_tickers
        assert "QUALIFIED_B" in winner_tickers

        # Verify descending sort order
        scores = [w.total_score for w in winners]
        assert scores == [120.0, 90.0, 75.1]

    def test_discord_embed_format(self):
        notifier = NotifierService()
        stock_rescued = create_dummy_scored_stock("RESCUED", 88.5, is_rescued=True)
        embed_rescued = notifier.format_discord_embed(stock_rescued)

        assert "RESCUED" in embed_rescued["title"]
        assert "88.5" in embed_rescued["title"]
        assert "🚨 [CATALYST RESCUE]" in embed_rescued["title"]
        assert embed_rescued["color"] == 0x2ECC71  # Emerald green tier

        stock_gold = create_dummy_scored_stock("GOLD", 95.0, is_rescued=False)
        embed_gold = notifier.format_discord_embed(stock_gold)
        assert embed_gold["color"] == 0xF1C40F  # Gold tier
        assert "🚀 [HIGH CONVICTION]" in embed_gold["title"]

        stock_diamond = create_dummy_scored_stock("DIAMOND", 125.0, is_rescued=False)
        embed_diamond = notifier.format_discord_embed(stock_diamond)
        assert embed_diamond["color"] == 0x9B59B6  # Purple tier

        field_names = [f["name"] for f in embed_rescued["fields"]]
        assert "📊 Total Score" in field_names
        assert "⚡ Mention Velocity" in field_names
        assert "🧠 LLM Sentiment" in field_names
        assert "🎯 Catalyst Trigger" in field_names
        assert "⚖️ Catalyst Rescue Active" in field_names
        assert "📐 Quantitative Math Breakdown" in field_names

    @patch("requests.post")
    def test_send_discord_webhook(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        notifier = NotifierService()
        stock = create_dummy_scored_stock("TEST", 95.0)

        success = notifier.send_discord_webhook(stock, webhook_url="https://discord.com/api/webhooks/dummy")
        assert success is True
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        assert "embeds" in call_kwargs["json"]

    def test_sns_publish_payload(self):
        notifier = NotifierService()
        mock_sns = MagicMock()
        notifier._sns_client = mock_sns

        stock = create_dummy_scored_stock("NVDA", 125.0)
        success = notifier.publish_to_sns(stock, topic_arn="arn:aws:sns:us-east-1:123456789012:test-topic")

        assert success is True
        assert mock_sns.publish.called
        call_kwargs = mock_sns.publish.call_args[1]

        assert call_kwargs["TopicArn"] == "arn:aws:sns:us-east-1:123456789012:test-topic"
        assert len(call_kwargs["Subject"]) <= 100
        message = json.loads(call_kwargs["Message"])
        assert message["ticker"] == "NVDA"
        assert message["total_score"] == 125.0

