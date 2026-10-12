"""Tests for Module 3: Quantitative Multi-Factor Scoring Engine."""

import pytest

from src.scoring_engine import (
    FundamentalDataProvider,
    FundamentalMetrics,
    MultiFactorScoringEngine,
    ScoreBreakdown,
)
from src.sentiment_parser import CatalystType


class TestScoringEngine:
    def test_profitable_large_cap_scoring(self):
        # Market Cap: > $2B (+15 pts)
        # Cash Flow: Positive (+15 pts)
        # Forward P/E: 22.0 < 25 (+10 pts)
        # Fundamental Subtotal: +40 pts
        # Velocity Spike: 550% > 500% (+30 pts)
        # Sentiment: 0.88 > 0.8 (+30 pts)
        # Catalyst: EARNINGS_BEAT (+35 pts)
        # Total Score: 40 + 60 + 35 = 135 pts
        mock_data = {
            "NVDA": FundamentalMetrics(
                market_cap=3_000_000_000_000,
                operating_cash_flow=40_000_000_000,
                forward_pe=22.0,
                company_name="NVIDIA Corporation"
            )
        }
        provider = FundamentalDataProvider(mock_data=mock_data)
        engine = MultiFactorScoringEngine(data_provider=provider)

        result = engine.score_ticker(
            ticker="NVDA",
            velocity_spike_pct=550.0,
            sentiment_polarity=0.88,
            catalyst=CatalystType.EARNINGS_BEAT.value
        )

        assert result.fundamental_score == 40
        assert result.breakdown.market_cap_pts == 15
        assert result.breakdown.profitability_pts == 15
        assert result.breakdown.valuation_pts == 10
        assert result.sentiment_score_points == 60
        assert result.catalyst_bonus == 35
        assert result.total_score == 135.0
        assert not result.breakdown.is_rescued_by_catalyst

    def test_speculative_microcap_penalty(self):
        # Micro-cap < $500M: -15 pts
        # Negative Cash Flow: -20 pts
        # Negative / No P/E: 0 pts
        # Fundamental Subtotal: -35 pts
        mock_data = {
            "FAIL": FundamentalMetrics(
                market_cap=120_000_000,
                operating_cash_flow=-15_000_000,
                forward_pe=None
            )
        }
        provider = FundamentalDataProvider(mock_data=mock_data)
        engine = MultiFactorScoringEngine(data_provider=provider)

        result = engine.score_ticker(
            ticker="FAIL",
            velocity_spike_pct=160.0,  # 150-300% -> +0 pts
            sentiment_polarity=0.30,   # Bearish < 0.4 -> -30 pts
            catalyst=CatalystType.NONE.value
        )

        assert result.fundamental_score == -35
        assert result.breakdown.market_cap_pts == -15
        assert result.breakdown.profitability_pts == -20
        assert result.breakdown.valuation_pts == 0
        assert result.sentiment_score_points == -30
        assert result.catalyst_bonus == 0
        assert result.total_score == -65.0
        assert not result.breakdown.is_rescued_by_catalyst

    def test_speculative_microcap_catalyst_rescue(self):
        # Unprofitable micro-cap begins with -35 pt fundamental deficit
        # High velocity (>500% -> +30) + Bullish sentiment (>0.8 -> +30) = +60 momentum
        # Net before catalyst = +25 pts
        # High-impact M&A_RUMOR catalyst (+50 pts)
        # Total Score: -35 + 60 + 50 = 75 pts
        mock_data = {
            "ACQR": FundamentalMetrics(
                market_cap=200_000_000,
                operating_cash_flow=-30_000_000,
                forward_pe=None,
                company_name="Acquiree Biotech"
            )
        }
        provider = FundamentalDataProvider(mock_data=mock_data)
        engine = MultiFactorScoringEngine(data_provider=provider)

        result = engine.score_ticker(
            ticker="ACQR",
            velocity_spike_pct=620.0,
            sentiment_polarity=0.92,
            catalyst=CatalystType.MA_RUMOR.value
        )

        assert result.fundamental_score == -35
        assert result.sentiment_score_points == 60
        assert result.catalyst_bonus == 50
        assert result.total_score == 75.0
        assert "Quantitative Math Breakdown for $ACQR" in result.breakdown.breakdown_text
        assert "M&A_RUMOR" in result.breakdown.breakdown_text

    def test_small_cap_catalyst_rescue_above_threshold(self):
        # Small-cap ($500M-$2B): +5 pts
        # Negative Cash Flow: -20 pts
        # Fundamental Subtotal: -15 pts
        # Velocity > 500%: +30 pts
        # Sentiment > 0.8: +30 pts
        # Catalyst M&A_RUMOR: +50 pts
        # Total Score: -15 + 60 + 50 = 95.0 pts (> 75 threshold)
        mock_data = {
            "MNA": FundamentalMetrics(
                market_cap=800_000_000,
                operating_cash_flow=-5_000_000,
                forward_pe=None
            )
        }
        provider = FundamentalDataProvider(mock_data=mock_data)
        engine = MultiFactorScoringEngine(data_provider=provider)

        result = engine.score_ticker(
            ticker="MNA",
            velocity_spike_pct=520.0,
            sentiment_polarity=0.85,
            catalyst="M&A_RUMOR"
        )

        assert result.fundamental_score == -15
        assert result.sentiment_score_points == 60
        assert result.catalyst_bonus == 50
        assert result.total_score == 95.0
        # Fundamental deficit < 0 and total > 75 -> rescued
        assert result.breakdown.is_rescued_by_catalyst

    def test_fda_approval_bonus(self):
        # Micro-cap with positive cash flow (e.g. licensing milestone)
        # Cap < $500M: -15 pts
        # Positive OCF: +15 pts
        # Valuation: 0 pts
        # Fundamental: 0 pts
        # Velocity > 500%: +30 pts
        # Sentiment > 0.8: +30 pts
        # FDA_APPROVAL: +40 pts
        # Total: 100.0 pts
        mock_data = {
            "BIOC": FundamentalMetrics(
                market_cap=350_000_000,
                operating_cash_flow=5_000_000,
                forward_pe=None
            )
        }
        provider = FundamentalDataProvider(mock_data=mock_data)
        engine = MultiFactorScoringEngine(data_provider=provider)

        result = engine.score_ticker(
            ticker="BIOC",
            velocity_spike_pct=700.0,
            sentiment_polarity=0.95,
            catalyst=CatalystType.FDA_APPROVAL.value
        )

        assert result.fundamental_score == 0
        assert result.sentiment_score_points == 60
        assert result.catalyst_bonus == 40
        assert result.total_score == 100.0

    def test_mega_cap_overvaluation_penalty(self):
        # A $1.5T Mega-Cap with Forward P/E = 160.0 (>50 and >80)
        # Market Cap: 0 pts (Saturated asset ceiling)
        # Cash flow: +15 pts
        # Forward P/E: -15 pts (Overvaluation penalty: no room to grow)
        # Net Fundamental Score: 0 pts!
        # With normal momentum (+40): Total Score = 40 pts (< 75 threshold)
        mock_data = {
            "OVERVAL": FundamentalMetrics(
                market_cap=1_500_000_000_000,
                operating_cash_flow=15_000_000_000,
                forward_pe=160.0,
                company_name="Mega Overvalued Corp"
            )
        }
        provider = FundamentalDataProvider(mock_data=mock_data)
        engine = MultiFactorScoringEngine(data_provider=provider)

        result = engine.score_ticker(
            ticker="OVERVAL",
            velocity_spike_pct=350.0,
            sentiment_polarity=0.82,
            catalyst=CatalystType.NONE.value
        )

        assert result.breakdown.market_cap_pts == 0
        assert result.breakdown.profitability_pts == 15
        assert result.breakdown.overvaluation_penalty_pts == -15
        assert result.fundamental_score == 0
        assert "Overvaluation Penalty" in result.breakdown.breakdown_text

    def test_undervalued_growth_compounder_bonus(self):
        # High growth mid-cap ($8B) with Forward P/E 18.0 < 25 and 35% YoY Revenue Growth
        # Market Cap: +15 pts
        # Cash flow: +15 pts
        # Forward P/E: +10 pts + 5 pts growth bonus = +15 pts
        # Fundamental Score: +45 pts!
        mock_data = {
            "GROWTH": FundamentalMetrics(
                market_cap=8_000_000_000,
                operating_cash_flow=800_000_000,
                forward_pe=18.0,
                revenue_growth=0.35,
                company_name="Rapid Scale Inc"
            )
        }
        provider = FundamentalDataProvider(mock_data=mock_data)
        engine = MultiFactorScoringEngine(data_provider=provider)

        result = engine.score_ticker(
            ticker="GROWTH",
            velocity_spike_pct=600.0,
            sentiment_polarity=0.90,
            catalyst=CatalystType.EARNINGS_BEAT.value
        )

        assert result.breakdown.market_cap_pts == 15
        assert result.breakdown.profitability_pts == 15
        assert result.breakdown.valuation_pts == 10
        assert result.breakdown.growth_bonus_pts == 5
        assert result.fundamental_score == 45
        assert result.total_score == 140.0
        assert "Undervalued Compounder" in result.breakdown.breakdown_text

