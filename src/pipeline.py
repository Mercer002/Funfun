"""
Pipeline Orchestration
Stitches together Modules 1 through 4 into a unified event-driven pipeline execution.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from typing import Any, Dict, List, Optional

from src.config import Settings, settings
from src.ingestion_lambda import FlaggedTicker, ScrapedPost, SocialIngestionService
from src.notifier import NotifierService
from src.scoring_engine import MultiFactorScoringEngine, ScoredStock
from src.sentiment_parser import SentimentParser

logger = logging.getLogger(__name__)


class PipelineRunner:
    """Orchestrates ingestion, catalyst extraction, scoring, and notification routing."""

    def __init__(
        self,
        config: Optional[Settings] = None,
        ingestion_service: Optional[SocialIngestionService] = None,
        sentiment_parser: Optional[SentimentParser] = None,
        scoring_engine: Optional[MultiFactorScoringEngine] = None,
        notifier_service: Optional[NotifierService] = None
    ):
        self.config = config or settings
        self.ingestion_service = ingestion_service or SocialIngestionService(config=self.config)
        self.sentiment_parser = sentiment_parser or SentimentParser(config=self.config)
        self.scoring_engine = scoring_engine or MultiFactorScoringEngine()
        self.notifier_service = notifier_service or NotifierService(config=self.config)

    def run(
        self,
        custom_posts: Optional[List[ScrapedPost]] = None,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Executes the full pipeline:
        1. Social Ingestion & DynamoDB 7-day velocity filtering (>150% spike)
        2. Groq Llama 3 catalyst & sentiment analysis
        3. Fundamental data retrieval (yfinance) & multi-factor scoring
        4. Winner filtering (>75 points) & alert dispatch (Discord / SNS)
        """
        logger.info("Starting Multi-Factor Stock Scoring Engine v2.0 Pipeline...")

        # ── Step 1: Ingestion & Soft Filtering ─────────────────────────
        flagged_tickers: List[FlaggedTicker] = self.ingestion_service.process_and_filter(
            posts=custom_posts
        )
        logger.info("Module 1 completed: %d tickers passed >150%% velocity spike", len(flagged_tickers))

        if not flagged_tickers:
            logger.info("No tickers exceeded the volume spike threshold. Pipeline execution finished.")
            return {
                "status": "success",
                "message": "No tickers passed velocity spike threshold",
                "flagged_count": 0,
                "scored_count": 0,
                "winners_count": 0,
                "winners": []
            }

        # ── Step 2 & 3: Catalyst Extraction & Scoring (Concurrent) ──────
        def _process_ticker(ft: FlaggedTicker) -> ScoredStock:
            # Module 2: Groq Llama 3 Catalyst Extraction
            analysis = self.sentiment_parser.parse_catalyst(ft.ticker, ft.aggregated_text)
            logger.info(
                "Module 2 [%s]: Sentiment=%.2f, Catalyst=%s (%s)",
                ft.ticker,
                analysis.sentiment_score,
                analysis.catalyst.value,
                analysis.reasoning
            )

            # Module 3: Quantitative Scoring Engine
            scored = self.scoring_engine.score_ticker(
                ticker=ft.ticker,
                velocity_spike_pct=ft.velocity_spike_pct,
                sentiment_polarity=analysis.sentiment_score,
                catalyst=analysis.catalyst.value,
                catalyst_reasoning=analysis.reasoning,
                aggregated_text=ft.aggregated_text
            )
            logger.info(
                "Module 3 [%s]: Total Score=%.1f (Fund=%+d, Sent=%+d, Cat=%+d)",
                scored.ticker,
                scored.total_score,
                scored.fundamental_score,
                scored.sentiment_score_points,
                scored.catalyst_bonus
            )
            return scored

        scored_stocks: List[ScoredStock] = []
        max_workers = min(8, len(flagged_tickers))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_process_ticker, ft) for ft in flagged_tickers]
            for future in as_completed(futures):
                try:
                    scored_stocks.append(future.result())
                except Exception as exc:
                    logger.error("Error processing flagged ticker: %s", exc)

        # ── Step 4: Output & Routing ──────────────────────────────────
        if dry_run:
            logger.info("Dry-run enabled: skipping real Discord/SNS network dispatch.")
            winners = self.notifier_service.filter_winning_stocks(scored_stocks)
            routing_summary = {
                "threshold": self.config.total_score_threshold,
                "total_evaluated": len(scored_stocks),
                "winners_count": len(winners),
                "winners": [
                    {
                        "ticker": s.ticker,
                        "total_score": s.total_score,
                        "catalyst": s.catalyst,
                        "is_rescued_by_catalyst": s.breakdown.is_rescued_by_catalyst,
                        "discord_sent": False,
                        "sns_sent": False,
                        "math_breakdown": s.breakdown.breakdown_text
                    }
                    for s in winners
                ]
            }
        else:
            routing_summary = self.notifier_service.route_winners(scored_stocks)

        logger.info(
            "Module 4 completed: %d / %d stocks qualified (>%.1f pts)",
            routing_summary["winners_count"],
            len(scored_stocks),
            self.config.total_score_threshold
        )

        return {
            "status": "success",
            "flagged_count": len(flagged_tickers),
            "scored_count": len(scored_stocks),
            "winners_count": routing_summary["winners_count"],
            "winners": routing_summary["winners"],
            "all_scores": [
                {
                    "ticker": s.ticker,
                    "total_score": s.total_score,
                    "fundamental_score": s.fundamental_score,
                    "sentiment_points": s.sentiment_score_points,
                    "catalyst_bonus": s.catalyst_bonus,
                    "catalyst": s.catalyst,
                    "is_rescued": s.breakdown.is_rescued_by_catalyst
                }
                for s in scored_stocks
            ]
        }

