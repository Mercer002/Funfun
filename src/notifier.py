"""
Module 4: Output & Routing
Filters tickers by Total_Score > 75, ranks them in descending order,
formats winning payloads with the complete mathematical breakdown,
and routes alerts to a Discord Webhook and/or Amazon SNS topic.
"""

from dataclasses import asdict
import json
import logging
from typing import Any, Dict, List, Optional
import boto3
from botocore.exceptions import ClientError
import requests

from src.config import Settings, settings
from src.scoring_engine import ScoredStock

logger = logging.getLogger(__name__)


class NotifierService:
    """Manages routing of winning stock alerts to Discord and AWS SNS."""

    def __init__(self, config: Optional[Settings] = None):
        self.config = config or settings
        self._sns_client = None

    @property
    def sns_client(self):
        if self._sns_client is None:
            self._sns_client = boto3.client("sns", region_name=self.config.aws_region)
        return self._sns_client

    def filter_winning_stocks(
        self, scored_stocks: List[ScoredStock], threshold: Optional[float] = None
    ) -> List[ScoredStock]:
        """
        Filters tickers strictly scoring > threshold (default: 75.0)
        and sorts them descending by Total_Score.
        """
        min_threshold = threshold if threshold is not None else self.config.total_score_threshold
        winners = [s for s in scored_stocks if s.total_score > min_threshold]
        winners.sort(key=lambda s: s.total_score, reverse=True)
        return winners

    def format_discord_embed(self, stock: ScoredStock) -> Dict[str, Any]:
        """Formats a ScoredStock into a Discord rich embed structure."""
        # Color tiering: Purple for exceptional (>110), Gold for high (>90), Emerald Green for qualified (>75)
        if stock.total_score >= 110.0:
            embed_color = 0x9B59B6  # Purple
        elif stock.total_score >= 90.0:
            embed_color = 0xF1C40F  # Gold
        else:
            embed_color = 0x2ECC71  # Emerald Green

        cap_str = (
            f"${stock.fundamentals.market_cap / 1e9:.2f}B"
            if stock.fundamentals.market_cap and stock.fundamentals.market_cap >= 1e9
            else f"${(stock.fundamentals.market_cap or 0) / 1e6:.1f}M"
        )
        pe_str = f"{stock.fundamentals.forward_pe:.1f}" if stock.fundamentals.forward_pe else "N/A"

        fields = [
            {
                "name": "📊 Total Score",
                "value": f"**{stock.total_score:.1f} / 100+**",
                "inline": True
            },
            {
                "name": "⚡ Mention Velocity",
                "value": f"+{stock.velocity_spike_pct:.1f}% (+{stock.breakdown.velocity_pts} pts)",
                "inline": True
            },
            {
                "name": "🧠 LLM Sentiment",
                "value": f"{stock.sentiment_polarity:.2f} (+{stock.breakdown.sentiment_pts} pts)",
                "inline": True
            },
            {
                "name": "🏢 Market Cap / P/E",
                "value": f"{cap_str} | Forward P/E: {pe_str}",
                "inline": True
            },
            {
                "name": "🎯 Catalyst Trigger",
                "value": f"`{stock.catalyst}` (+{stock.catalyst_bonus} pts)",
                "inline": True
            },
            {
                "name": "⚖️ Catalyst Rescue Active",
                "value": "✅ YES (Negative Fundamentals Offset)" if stock.breakdown.is_rescued_by_catalyst else "No (Organic)",
                "inline": True
            }
        ]

        if stock.catalyst_reasoning:
            fields.append({
                "name": "🔍 Qualitative Catalyst Insight",
                "value": stock.catalyst_reasoning[:500],
                "inline": False
            })

        fields.append({
            "name": "📐 Quantitative Math Breakdown",
            "value": f"```{stock.breakdown.breakdown_text}```"[:1024],
            "inline": False
        })

        title_prefix = "🚨 [CATALYST RESCUE]" if stock.breakdown.is_rescued_by_catalyst else "🚀 [HIGH CONVICTION]"

        return {
            "title": f"{title_prefix} ${stock.ticker} (Score: {stock.total_score:.1f} pts)",
            "description": f"Company: **{stock.fundamentals.company_name or stock.ticker}**\nAutomated post-market momentum & catalyst screen.",
            "color": embed_color,
            "fields": fields,
            "footer": {
                "text": "Multi-Factor Stock Scoring Engine v2.0 • Event-Driven Serverless Pipeline"
            }
        }

    def send_discord_webhook(
        self, stock: ScoredStock, webhook_url: Optional[str] = None
    ) -> bool:
        """Sends rich embed alert to Discord Webhook."""
        url = webhook_url or self.config.discord_webhook_url
        if not url:
            logger.info("Discord webhook URL not configured. Skipping Discord notification for %s.", stock.ticker)
            return False

        payload = {
            "username": "Stock Scoring Engine v2.0",
            "avatar_url": "https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/1f4c8.png",
            "embeds": [self.format_discord_embed(stock)]
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info("Successfully posted Discord alert for %s", stock.ticker)
            return True
        except Exception as exc:
            logger.error("Failed to post alert to Discord for %s: %s", stock.ticker, exc)
            return False

    def publish_to_sns(
        self, stock: ScoredStock, topic_arn: Optional[str] = None
    ) -> bool:
        """Publishes winning payload to Amazon SNS Topic."""
        target_arn = topic_arn or self.config.sns_topic_arn
        if not target_arn:
            logger.info("SNS Topic ARN not configured. Skipping SNS notification for %s.", stock.ticker)
            return False

        subject = f"[Stock Alert] ${stock.ticker} High Conviction Score: {stock.total_score:.1f}"
        # Truncate subject to 100 chars (AWS SNS subject limit)
        subject = subject[:100]

        message_body = {
            "ticker": stock.ticker,
            "total_score": stock.total_score,
            "fundamental_score": stock.fundamental_score,
            "sentiment_score_points": stock.sentiment_score_points,
            "catalyst_bonus": stock.catalyst_bonus,
            "catalyst": stock.catalyst,
            "catalyst_reasoning": stock.catalyst_reasoning,
            "is_rescued_by_catalyst": stock.breakdown.is_rescued_by_catalyst,
            "math_breakdown": stock.breakdown.breakdown_text,
            "market_cap": stock.fundamentals.market_cap,
            "forward_pe": stock.fundamentals.forward_pe,
            "operating_cash_flow": stock.fundamentals.operating_cash_flow
        }

        try:
            self.sns_client.publish(
                TopicArn=target_arn,
                Subject=subject,
                Message=json.dumps(message_body, indent=2)
            )
            logger.info("Successfully published SNS notification for %s to %s", stock.ticker, target_arn)
            return True
        except (ClientError, Exception) as exc:
            logger.error("Failed to publish SNS alert for %s: %s", stock.ticker, exc)
            return False

    def route_winners(
        self, scored_stocks: List[ScoredStock]
    ) -> Dict[str, Any]:
        """
        Main routing function: Filters tickers > 75, publishes alerts,
        and returns processing summary.
        """
        winners = self.filter_winning_stocks(scored_stocks)
        routed_results = []

        for stock in winners:
            discord_sent = self.send_discord_webhook(stock)
            sns_sent = self.publish_to_sns(stock)

            routed_results.append({
                "ticker": stock.ticker,
                "total_score": stock.total_score,
                "catalyst": stock.catalyst,
                "is_rescued_by_catalyst": stock.breakdown.is_rescued_by_catalyst,
                "discord_sent": discord_sent,
                "sns_sent": sns_sent,
                "math_breakdown": stock.breakdown.breakdown_text
            })

        return {
            "threshold": self.config.total_score_threshold,
            "total_evaluated": len(scored_stocks),
            "winners_count": len(winners),
            "winners": routed_results
        }

