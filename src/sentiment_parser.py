"""
Module 2: LLM Catalyst Extraction
Sends aggregated social text for flagged tickers to Groq (Llama 3) to extract
sentiment polarity (0.0 to 1.0) and high-impact qualitative catalysts using
a strict JSON schema.
"""

from enum import Enum
import json
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator

from src.config import Settings, settings
from src.utils import retry_with_backoff

logger = logging.getLogger(__name__)


class CatalystType(str, Enum):
    """Specific high-conviction catalyst categories."""
    MA_RUMOR = "M&A_RUMOR"
    EARNINGS_BEAT = "EARNINGS_BEAT"
    GUIDANCE_RAISE = "GUIDANCE_RAISE"
    FDA_APPROVAL = "FDA_APPROVAL"
    PATENT_GRANT = "PATENT_GRANT"
    PRODUCT_LAUNCH = "PRODUCT_LAUNCH"
    NONE = "NONE"


class CatalystAnalysis(BaseModel):
    """Strict JSON schema output for Groq LLM inference."""
    sentiment_score: float = Field(
        ...,
        description="Sentiment polarity score bounded strictly between 0.0 (bearish) and 1.0 (bullish)"
    )
    catalyst: CatalystType = Field(
        default=CatalystType.NONE,
        description="Detected high-conviction catalyst"
    )
    reasoning: str = Field(
        default="",
        description="Brief justification of the sentiment and catalyst determination"
    )

    @field_validator("sentiment_score", mode="before")
    @classmethod
    def clamp_sentiment(cls, v: Any) -> float:
        try:
            val = float(v)
            return max(0.0, min(1.0, round(val, 3)))
        except (ValueError, TypeError):
            return 0.5


SYSTEM_PROMPT = """You are an expert quantitative financial analyst specializing in social sentiment and qualitative catalyst extraction.
Analyze the provided forum discussions for the target stock ticker.

Evaluate:
1. Overall sentiment polarity: score from 0.0 (extremely bearish/pessimistic) to 1.0 (extremely bullish/optimistic).
   - Highly Bullish: > 0.8
   - Neutral/Mixed: 0.4 to 0.8
   - Bearish: < 0.4
2. Specific high-conviction qualitative catalysts:
   - "M&A_RUMOR": Rumors or reports of mergers, acquisitions, takeovers, or buyout bids.
   - "EARNINGS_BEAT": Confirmed or reported quarterly earnings/revenue beat.
   - "GUIDANCE_RAISE": Management raising forward revenue/EPS guidance.
   - "FDA_APPROVAL": Drug, therapy, or medical device approval from regulatory bodies (FDA, EMA).
   - "PATENT_GRANT": Major intellectual property or patent approval.
   - "PRODUCT_LAUNCH": Significant new product or service commercial release.
   - "NONE": General hype, pump and dump, meme banter, technical trading, or no verified catalyst.

Output ONLY a JSON object matching this exact schema:
{
  "sentiment_score": <float between 0.0 and 1.0>,
  "catalyst": "M&A_RUMOR" | "EARNINGS_BEAT" | "GUIDANCE_RAISE" | "FDA_APPROVAL" | "PATENT_GRANT" | "PRODUCT_LAUNCH" | "NONE",
  "reasoning": "<concise explanation>"
}
"""


class SentimentParser:
    """Invokes Groq API with Llama 3 to analyze sentiment and catalysts."""

    def __init__(self, config: Optional[Settings] = None):
        self.config = config or settings
        self._groq_client = None

    @property
    def groq_client(self):
        if self._groq_client is None and self.config.groq_api_key:
            try:
                from groq import Groq  # type: ignore
                self._groq_client = Groq(api_key=self.config.groq_api_key)
            except Exception as exc:
                logger.warning("Could not initialize Groq client: %s", exc)
        return self._groq_client

    def parse_catalyst(self, ticker: str, text: str) -> CatalystAnalysis:
        """
        Extracts sentiment and catalyst from aggregated text for a ticker.
        Uses Groq Llama 3 when API key is provided, with deterministic fallback for offline/tests.
        """
        if not text or not text.strip():
            return CatalystAnalysis(
                sentiment_score=0.5,
                catalyst=CatalystType.NONE,
                reasoning="No social discussion text available."
            )

        # Try Groq API if client is available
        if self.groq_client:
            try:
                return self._call_groq(ticker, text)
            except Exception as exc:
                logger.warning("Groq API call failed for %s (%s). Using heuristic fallback.", ticker, exc)

        # Fallback heuristic parser (also used for mock/testing)
        return self._heuristic_analysis(ticker, text)

    @retry_with_backoff(retries=2, backoff_in_seconds=1.0)
    def _call_groq(self, ticker: str, text: str) -> CatalystAnalysis:
        """Makes request to Groq API with JSON mode enabled."""
        user_message = f"Target Stock Ticker: ${ticker}\n\nAggregated Social Discussions:\n{text[:3500]}"

        completion = self.groq_client.chat.completions.create(
            model=self.config.groq_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=300
        )

        raw_response = completion.choices[0].message.content
        data = json.loads(raw_response)

        # Normalize catalyst enum string
        catalyst_str = str(data.get("catalyst", "NONE")).upper().strip()
        matched_catalyst = CatalystType.NONE
        for cat in CatalystType:
            if cat.value == catalyst_str or cat.name == catalyst_str:
                matched_catalyst = cat
                break

        return CatalystAnalysis(
            sentiment_score=float(data.get("sentiment_score", 0.5)),
            catalyst=matched_catalyst,
            reasoning=str(data.get("reasoning", ""))
        )

    def _heuristic_analysis(self, ticker: str, text: str) -> CatalystAnalysis:
        """Deterministic keyword parser for offline mode, testing, and API fallbacks."""
        lower_text = text.lower()

        # Bearish indicators
        bearish_keywords = ["bankruptcy", "chapter 11", "dilution", "fraud", "investigation", "delist", "short seller report", "margin compression"]
        if any(w in lower_text for w in bearish_keywords):
            return CatalystAnalysis(
                sentiment_score=0.20,
                catalyst=CatalystType.NONE,
                reasoning="Significant bearish risks, bankruptcy, or margin compression mentioned."
            )

        # High-impact catalysts
        if any(w in lower_text for w in ["acquire", "acquisition", "buyout", "merger", "m&a", "takeover"]):
            return CatalystAnalysis(
                sentiment_score=0.90,
                catalyst=CatalystType.MA_RUMOR,
                reasoning="Discussions highlight potential buyout or M&A acquisition rumors."
            )

        if any(w in lower_text for w in ["fda approval", "fda approved", "phase 3", "breakthrough therapy"]):
            return CatalystAnalysis(
                sentiment_score=0.92,
                catalyst=CatalystType.FDA_APPROVAL,
                reasoning="Discussion highlights clinical trial success or FDA approval milestone."
            )

        if any(w in lower_text for w in ["patent grant", "patent approved", "patented technology"]):
            return CatalystAnalysis(
                sentiment_score=0.88,
                catalyst=CatalystType.PATENT_GRANT,
                reasoning="Intellectual property patent grant recognized."
            )

        if any(w in lower_text for w in ["guidance raise", "raised guidance", "guidance hike"]):
            return CatalystAnalysis(
                sentiment_score=0.86,
                catalyst=CatalystType.GUIDANCE_RAISE,
                reasoning="Company raised forward financial outlook and guidance."
            )

        if any(w in lower_text for w in ["earnings beat", "crushes earnings", "crushed earnings", "record revenue", "eps beat"]):
            return CatalystAnalysis(
                sentiment_score=0.85,
                catalyst=CatalystType.EARNINGS_BEAT,
                reasoning="Discussion references strong quarterly earnings and revenue beat."
            )

        if any(w in lower_text for w in ["product launch", "unveiled new", "commercial release"]):
            return CatalystAnalysis(
                sentiment_score=0.78,
                catalyst=CatalystType.PRODUCT_LAUNCH,
                reasoning="New product line or feature release mentioned."
            )

        # General bullish or neutral
        if any(w in lower_text for w in ["moon", "surge", "breakout", "bullish", "call", "calls"]):
            return CatalystAnalysis(
                sentiment_score=0.75,
                catalyst=CatalystType.NONE,
                reasoning="General bullish momentum and call option interest without specific catalyst."
            )

        return CatalystAnalysis(
            sentiment_score=0.50,
            catalyst=CatalystType.NONE,
            reasoning="Neutral social chatter with no distinct catalyst."
        )


def analyze_flagged_tickers(
    flagged_tickers: List[Dict[str, Any]], parser: Optional[SentimentParser] = None
) -> List[Dict[str, Any]]:
    """Analyzes a list of flagged tickers and augments each with LLM sentiment & catalyst."""
    parser = parser or SentimentParser()
    enriched = []

    for item in flagged_tickers:
        ticker = item.get("ticker", "")
        text = item.get("aggregated_text", "")
        analysis = parser.parse_catalyst(ticker, text)

        enriched.append({
            **item,
            "sentiment_score": analysis.sentiment_score,
            "catalyst": analysis.catalyst.value,
            "catalyst_reasoning": analysis.reasoning
        })

    return enriched
