"""
Module 3: The Multi-Factor Scoring Engine
Queries yfinance for fundamental financial data, evaluates balance sheet and valuation
metrics, applies point penalties and momentum bonuses, and layers unbounded catalyst multipliers
to produce a composite quantitative score and math breakdown.
"""

from dataclasses import dataclass, field
import logging
from typing import Any, Dict, Optional, Tuple

import yfinance as yf

from src.sentiment_parser import CatalystType
from src.utils import retry_with_backoff

logger = logging.getLogger(__name__)


@dataclass
class FundamentalMetrics:
    market_cap: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    net_income: Optional[float] = None
    forward_pe: Optional[float] = None
    trailing_pe: Optional[float] = None
    revenue_growth: Optional[float] = None
    peg_ratio: Optional[float] = None
    earnings_growth: Optional[float] = None
    currency: str = "USD"
    company_name: str = ""


@dataclass
class ScoreBreakdown:
    # Fundamental points
    market_cap_pts: int = 0
    profitability_pts: int = 0
    valuation_pts: int = 0
    growth_bonus_pts: int = 0
    overvaluation_penalty_pts: int = 0
    fundamental_subtotal: int = 0

    # Sentiment & Momentum points
    velocity_pts: int = 0
    sentiment_pts: int = 0
    sentiment_subtotal: int = 0

    # Catalyst bonus
    catalyst_bonus_pts: int = 0
    catalyst_type: str = CatalystType.NONE.value

    # Composite Total
    total_score: float = 0.0

    # Detailed human-readable math breakdown
    breakdown_text: str = ""
    is_rescued_by_catalyst: bool = False


@dataclass
class ScoredStock:
    ticker: str
    total_score: float
    fundamental_score: int
    sentiment_score_points: int
    catalyst_bonus: int
    sentiment_polarity: float
    velocity_spike_pct: float
    catalyst: str
    catalyst_reasoning: str
    fundamentals: FundamentalMetrics
    breakdown: ScoreBreakdown
    aggregated_text: str = ""


class FundamentalDataProvider:
    """Retrieves fundamental financial metrics from yfinance with mock support."""

    def __init__(self, mock_data: Optional[Dict[str, FundamentalMetrics]] = None):
        self.mock_data = mock_data or {}

    @retry_with_backoff(retries=2, backoff_in_seconds=1.0)
    def _fetch_from_yfinance(self, ticker: str) -> FundamentalMetrics:
        """Internal call to yfinance wrapped with exponential backoff."""
        yticker = yf.Ticker(ticker)
        info = yticker.info or {}

        market_cap = info.get("marketCap")
        ocf = info.get("operatingCashflow")
        net_income = info.get("netIncomeToCommon")
        forward_pe = info.get("forwardPE")
        trailing_pe = info.get("trailingPE")
        revenue_growth = info.get("revenueGrowth")
        peg_ratio = info.get("pegRatio")
        earnings_growth = info.get("earningsGrowth")
        currency = info.get("currency", "USD")
        company_name = info.get("shortName") or info.get("longName") or ticker

        return FundamentalMetrics(
            market_cap=float(market_cap) if market_cap is not None else None,
            operating_cash_flow=float(ocf) if ocf is not None else None,
            net_income=float(net_income) if net_income is not None else None,
            forward_pe=float(forward_pe) if forward_pe is not None else None,
            trailing_pe=float(trailing_pe) if trailing_pe is not None else None,
            revenue_growth=float(revenue_growth) if revenue_growth is not None else None,
            peg_ratio=float(peg_ratio) if peg_ratio is not None else None,
            earnings_growth=float(earnings_growth) if earnings_growth is not None else None,
            currency=currency,
            company_name=company_name
        )

    def get_fundamentals(self, ticker: str) -> FundamentalMetrics:
        """Fetch fundamental data for ticker."""
        if ticker in self.mock_data:
            return self.mock_data[ticker]

        try:
            return self._fetch_from_yfinance(ticker)
        except Exception as exc:
            logger.warning("Failed to retrieve yfinance fundamentals for %s: %s", ticker, exc)
            return FundamentalMetrics(company_name=ticker)


class MultiFactorScoringEngine:
    """Calculates multi-factor score across fundamentals, momentum, and catalysts."""

    def __init__(self, data_provider: Optional[FundamentalDataProvider] = None):
        self.data_provider = data_provider or FundamentalDataProvider()

    def score_ticker(
        self,
        ticker: str,
        velocity_spike_pct: float,
        sentiment_polarity: float,
        catalyst: str,
        catalyst_reasoning: str = "",
        aggregated_text: str = ""
    ) -> ScoredStock:
        """Runs the composite scoring model for a single flagged ticker."""
        fundamentals = self.data_provider.get_fundamentals(ticker)

        # ─────────────────────────────────────────────────────────────
        # A. Fundamental Score (Base: 40 points)
        # ─────────────────────────────────────────────────────────────
        # 1. Market Cap:
        #    > $2B: +15 pts
        #    $500M - $2B: +5 pts
        #    < $500M: -15 pts (Penalty)
        # 1. Market Cap Runway & Expansion Room:
        #    Sweet Spot / Mid-Cap ($500M-$30B) & Large-Cap ($30B-$300B): +15 pts
        #    Mega-Cap Saturated (> $300B) with Forward P/E > 50: +0 pts (Law of large numbers ceiling)
        #    Small-Cap ($500M - $2B): +5 pts
        #    Micro-Cap (< $500M): -15 pts (Penalty)
        cap = fundamentals.market_cap
        pe = fundamentals.forward_pe or fundamentals.trailing_pe
        rev_growth = fundamentals.revenue_growth
        peg = fundamentals.peg_ratio

        if cap is not None:
            if cap > 2_000_000_000:
                market_cap_pts = 15
                cap_desc = f"+15 pts (Large/Mid-Cap >$2B: ${cap/1e9:.2f}B)"
                if cap > 300_000_000_000 and pe is not None and pe > 50.0:
                    market_cap_pts = 0
                    cap_desc = f"+0 pts (Mega-Cap Saturated >$300B with Forward P/E {pe:.1f} > 50: Low multiple expansion runway: ${cap/1e9:.1f}B)"
                else:
                    market_cap_pts = 15
                    cap_desc = f"+15 pts (Large/Mid-Cap >$2B: ${cap/1e9:.2f}B)"
            elif cap >= 500_000_000:
                market_cap_pts = 5
                cap_desc = f"+5 pts (Small-Cap $500M-$2B: ${cap/1e6:.1f}M)"
            else:
                market_cap_pts = -15
                cap_desc = f"-15 pts (Micro-Cap <$500M Penalty: ${cap/1e6:.1f}M)"
        else:
            market_cap_pts = -15
            cap_desc = "-15 pts (Unknown / Micro-Cap Penalty)"

        # 2. Profitability (Operating Cash Flow / Net Income):
        #    Positive TTM Cash Flow: +15 pts
        #    Negative TTM Cash Flow: -20 pts (Penalty)
        cash_flow = fundamentals.operating_cash_flow
        if cash_flow is None:
            # Fallback to Net Income if OCF is missing
            cash_flow = fundamentals.net_income

        if cash_flow is not None:
            if cash_flow > 0:
                profitability_pts = 15
                prof_desc = f"+15 pts (Positive Cash Flow: +${cash_flow/1e6:.1f}M)"
            else:
                profitability_pts = -20
                prof_desc = f"-20 pts (Negative Cash Flow Penalty: -${abs(cash_flow)/1e6:.1f}M)"
        else:
            profitability_pts = -20
            prof_desc = "-20 pts (No Cash Flow / Unprofitable Penalty)"

        # 3. Valuation (Forward P/E):
        #    P/E < 25 (Reasonably valued): +10 pts
        # 3. Valuation (Forward P/E & Growth Acceleration):
        #    P/E < 25 (Reasonably valued): +10 pts (up to +15 pts with high revenue growth > 15% or PEG < 1.5)
        #    25 <= P/E <= 50: +5 pts
        #    P/E > 50 or Negative (Overvalued / Losing money): 0 pts
        pe = fundamentals.forward_pe or fundamentals.trailing_pe
        #    P/E > 50: 0 pts
        #    Forward P/E > 80 or Mega-Cap > $300B with P/E > 50: -15 pts (Severe Overvaluation Penalty)
        #    Negative or Unassigned P/E: 0 pts
        growth_bonus_pts = 0
        overvaluation_penalty_pts = 0

        if pe is not None and pe > 0:
            if pe < 25.0:
                valuation_pts = 10
                val_desc = f"+10 pts (Reasonable Valuation: Forward P/E {pe:.1f} < 25)"
                if rev_growth is not None and rev_growth > 0.15:
                    growth_bonus_pts = 5
                    val_desc = f"+15 pts (Undervalued Compounder: Forward P/E {pe:.1f} < 25 with {rev_growth*100:.1f}% YoY Growth)"
                elif peg is not None and peg < 1.5:
                    growth_bonus_pts = 5
                    val_desc = f"+15 pts (Growth at Reasonable Price: Forward P/E {pe:.1f} < 25, PEG {peg:.2f} < 1.5)"
                else:
                    val_desc = f"+10 pts (Reasonable Valuation: Forward P/E {pe:.1f} < 25)"
            elif pe <= 50.0:
                valuation_pts = 5
                val_desc = f"+5 pts (Moderate Valuation: Forward P/E {pe:.1f})"
            else:
                valuation_pts = 0
                val_desc = f"+0 pts (High Valuation: Forward P/E {pe:.1f} > 50)"
                if pe > 80.0 or (cap is not None and cap > 300_000_000_000 and pe > 50.0):
                    overvaluation_penalty_pts = -15
                    val_desc = f"-15 pts (Overvaluation Penalty: Forward P/E {pe:.1f} > 50 on Saturated Asset with low runway)"
                else:
                    val_desc = f"+0 pts (High Valuation: Forward P/E {pe:.1f} > 50)"
        else:
            valuation_pts = 0
            val_desc = "+0 pts (Negative or Unassigned P/E: Unprofitable / High Risk)"

        fundamental_subtotal = market_cap_pts + profitability_pts + valuation_pts
        fundamental_subtotal = (
            market_cap_pts + profitability_pts + valuation_pts + growth_bonus_pts + overvaluation_penalty_pts
        )

        # ─────────────────────────────────────────────────────────────
        # B. Sentiment & Momentum Score (Base: 60 points)
        # ─────────────────────────────────────────────────────────────
        # 1. Mention Velocity:
        #    > 500% spike: +30 pts
        #    300% - 500% spike: +15 pts
        #    150% - 300% spike: 0 pts
        if velocity_spike_pct > 500.0:
            velocity_pts = 30
            vel_desc = f"+30 pts (Extreme Velocity Spike: {velocity_spike_pct:.1f}% > 500%)"
        elif velocity_spike_pct >= 300.0:
            velocity_pts = 15
            vel_desc = f"+15 pts (Strong Velocity Spike: {velocity_spike_pct:.1f}%)"
        else:
            velocity_pts = 0
            vel_desc = f"+0 pts (Moderate Velocity Spike: {velocity_spike_pct:.1f}%)"

        # 2. LLM Sentiment Polarity:
        #    Highly Bullish (score > 0.8): +30 pts
        #    Neutral/Mixed (0.4 <= score <= 0.8): +10 pts
        #    Bearish (score < 0.4): -30 pts (Penalty)
        if sentiment_polarity > 0.8:
            sentiment_pts = 30
            sent_desc = f"+30 pts (Highly Bullish Sentiment: {sentiment_polarity:.2f} > 0.80)"
        elif sentiment_polarity >= 0.4:
            sentiment_pts = 10
            sent_desc = f"+10 pts (Neutral/Mixed Sentiment: {sentiment_polarity:.2f})"
        else:
            sentiment_pts = -30
            sent_desc = f"-30 pts (Bearish Sentiment Penalty: {sentiment_polarity:.2f} < 0.40)"

        sentiment_subtotal = velocity_pts + sentiment_pts

        # ─────────────────────────────────────────────────────────────
        # C. Catalyst Multipliers (Unbounded Bonuses)
        # ─────────────────────────────────────────────────────────────
        # M&A_RUMOR: +50 pts
        # FDA_APPROVAL / PATENT_GRANT: +40 pts
        # EARNINGS_BEAT / GUIDANCE_RAISE: +35 pts
        # PRODUCT_LAUNCH: +20 pts
        # NONE: 0 pts
        cat_upper = catalyst.upper().strip()
        if cat_upper in [CatalystType.MA_RUMOR.value, "M&A_RUMOR", "MA_RUMOR", "BUYOUT"]:
            catalyst_bonus_pts = 50
            cat_desc = "+50 pts [M&A_RUMOR: Acquisition / Buyout Target Override]"
        elif cat_upper in [CatalystType.FDA_APPROVAL.value, CatalystType.PATENT_GRANT.value, "FDA_APPROVAL", "PATENT_GRANT"]:
            catalyst_bonus_pts = 40
            cat_desc = f"+40 pts [{cat_upper}: Regulatory Approval / Patent Grant]"
        elif cat_upper in [CatalystType.EARNINGS_BEAT.value, CatalystType.GUIDANCE_RAISE.value, "EARNINGS_BEAT", "GUIDANCE_RAISE"]:
            catalyst_bonus_pts = 35
            cat_desc = f"+35 pts [{cat_upper}: Proven Fundamental Inflection]"
        elif cat_upper in [CatalystType.PRODUCT_LAUNCH.value, "PRODUCT_LAUNCH"]:
            catalyst_bonus_pts = 20
            cat_desc = "+20 pts [PRODUCT_LAUNCH: Commercial Release]"
        else:
            catalyst_bonus_pts = 0
            cat_desc = "+0 pts [NONE: No High-Impact Catalyst Identified]"

        # ─────────────────────────────────────────────────────────────
        # Composite Calculation & Math Breakdown
        # ─────────────────────────────────────────────────────────────
        total_score = float(fundamental_subtotal + sentiment_subtotal + catalyst_bonus_pts)
        is_rescued = (fundamental_subtotal < 0) and (total_score > 75.0)

        breakdown_text = (
            f"**Quantitative Math Breakdown for ${ticker}**\n"
            f"1. Fundamental Score ({fundamental_subtotal:+d} pts):\n"
            f"   • Market Cap: {cap_desc}\n"
            f"   • Profitability: {prof_desc}\n"
            f"   • Valuation: {val_desc}\n"
            f"2. Sentiment & Momentum Score ({sentiment_subtotal:+d} pts):\n"
            f"   • Mention Velocity: {vel_desc}\n"
            f"   • LLM Polarity: {sent_desc}\n"
            f"3. Catalyst Multipliers ({catalyst_bonus_pts:+d} pts):\n"
            f"   • Catalyst: {cat_desc}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Total Score: {total_score:.1f} pts**"
            f"{' 🚀 [CATALYST RESCUE: Overrode Fundamental Penalty]' if is_rescued else ''}"
        )

        breakdown = ScoreBreakdown(
            market_cap_pts=market_cap_pts,
            profitability_pts=profitability_pts,
            valuation_pts=valuation_pts,
            growth_bonus_pts=growth_bonus_pts,
            overvaluation_penalty_pts=overvaluation_penalty_pts,
            fundamental_subtotal=fundamental_subtotal,
            velocity_pts=velocity_pts,
            sentiment_pts=sentiment_pts,
            sentiment_subtotal=sentiment_subtotal,
            catalyst_bonus_pts=catalyst_bonus_pts,
            catalyst_type=catalyst,
            total_score=total_score,
            breakdown_text=breakdown_text,
            is_rescued_by_catalyst=is_rescued
        )

        return ScoredStock(
            ticker=ticker,
            total_score=total_score,
            fundamental_score=fundamental_subtotal,
            sentiment_score_points=sentiment_subtotal,
            catalyst_bonus=catalyst_bonus_pts,
            sentiment_polarity=sentiment_polarity,
            velocity_spike_pct=velocity_spike_pct,
            catalyst=catalyst,
            catalyst_reasoning=catalyst_reasoning,
            fundamentals=fundamentals,
            breakdown=breakdown,
            aggregated_text=aggregated_text
        )

