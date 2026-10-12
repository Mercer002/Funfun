"""Tests for Module 2: Groq Llama 3 Sentiment & Catalyst Extraction."""

import json
from unittest.mock import MagicMock
import pytest
from pydantic import ValidationError

from src.sentiment_parser import (
    CatalystAnalysis,
    CatalystType,
    SentimentParser,
    analyze_flagged_tickers,
)


class TestCatalystAnalysisSchema:
    def test_valid_schema(self):
        analysis = CatalystAnalysis(
            sentiment_score=0.85,
            catalyst=CatalystType.MA_RUMOR,
            reasoning="Merger rumors verified."
        )
        assert analysis.sentiment_score == 0.85
        assert analysis.catalyst == CatalystType.MA_RUMOR
        assert analysis.reasoning == "Merger rumors verified."

    def test_sentiment_score_clamping(self):
        # Negative sentiment clamped to 0.0
        a1 = CatalystAnalysis(sentiment_score=-0.5, catalyst=CatalystType.NONE)
        assert a1.sentiment_score == 0.0

        # Sentiment > 1.0 clamped to 1.0
        a2 = CatalystAnalysis(sentiment_score=1.5, catalyst=CatalystType.NONE)
        assert a2.sentiment_score == 1.0

    def test_all_catalyst_types(self):
        for cat in [
            CatalystType.MA_RUMOR,
            CatalystType.EARNINGS_BEAT,
            CatalystType.GUIDANCE_RAISE,
            CatalystType.FDA_APPROVAL,
            CatalystType.PATENT_GRANT,
            CatalystType.PRODUCT_LAUNCH,
            CatalystType.NONE,
        ]:
            analysis = CatalystAnalysis(sentiment_score=0.5, catalyst=cat)
            assert analysis.catalyst == cat


class TestHeuristicParser:
    def test_ma_rumor_detection(self):
        parser = SentimentParser()
        text = "Acquisition talks underway! Buyout firm offers $1.8B cash deal."
        res = parser.parse_catalyst("ACQR", text)
        assert res.catalyst == CatalystType.MA_RUMOR
        assert res.sentiment_score >= 0.80

    def test_fda_approval_detection(self):
        parser = SentimentParser()
        text = "Biotech receives FDA approval for breakthrough therapy after successful Phase 3 trial."
        res = parser.parse_catalyst("BIOC", text)
        assert res.catalyst == CatalystType.FDA_APPROVAL
        assert res.sentiment_score >= 0.80

    def test_patent_grant_detection(self):
        parser = SentimentParser()
        text = "Company announces patent grant on next-gen quantum computing architecture."
        res = parser.parse_catalyst("QNTM", text)
        assert res.catalyst == CatalystType.PATENT_GRANT
        assert res.sentiment_score >= 0.80

    def test_earnings_beat_detection(self):
        parser = SentimentParser()
        text = "Crushes earnings beat with record revenue and EPS of $2.50 vs expected $1.80."
        res = parser.parse_catalyst("NVDA", text)
        assert res.catalyst == CatalystType.EARNINGS_BEAT
        assert res.sentiment_score >= 0.80

    def test_guidance_raise_detection(self):
        parser = SentimentParser()
        text = "Management raised guidance for the entire fiscal year."
        res = parser.parse_catalyst("TECH", text)
        assert res.catalyst == CatalystType.GUIDANCE_RAISE
        assert res.sentiment_score >= 0.80

    def test_bearish_bankruptcy_detection(self):
        parser = SentimentParser()
        text = "Warning: Company files for Chapter 11 bankruptcy due to extreme dilution and fraud."
        res = parser.parse_catalyst("FAIL", text)
        assert res.catalyst == CatalystType.NONE
        assert res.sentiment_score < 0.40

    def test_empty_text_fallback(self):
        parser = SentimentParser()
        res = parser.parse_catalyst("EMPTY", "")
        assert res.catalyst == CatalystType.NONE
        assert res.sentiment_score == 0.5


class TestGroqApiIntegrationMock:
    def test_groq_mock_response_parsing(self):
        parser = SentimentParser()

        # Mock Groq client and chat completion
        mock_client = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "sentiment_score": 0.94,
            "catalyst": "M&A_RUMOR",
            "reasoning": "High-conviction acquisition target by sovereign wealth fund."
        })
        mock_completion = MagicMock(choices=[mock_choice])
        mock_client.chat.completions.create.return_value = mock_completion
        parser._groq_client = mock_client

        res = parser.parse_catalyst("ACQR", "Acquisition discussion online")
        assert res.sentiment_score == 0.94
        assert res.catalyst == CatalystType.MA_RUMOR
        assert "acquisition" in res.reasoning.lower()

    def test_groq_error_graceful_fallback(self):
        parser = SentimentParser()
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("Groq rate limit exceeded")
        parser._groq_client = mock_client

        # Should smoothly fall back to heuristic parser without crashing
        res = parser.parse_catalyst("BIOC", "FDA approved clinical trial therapy")
        assert res.catalyst == CatalystType.FDA_APPROVAL

