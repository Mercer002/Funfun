"""
Configuration settings for the Multi-Factor Stock Scoring Engine v2.0.
"""

from dataclasses import dataclass, field
import os
from typing import List, Set
from dotenv import load_dotenv

load_dotenv()


# Common non-ticker uppercase terms, slang, and broad market ETFs to exclude from regex extraction
DEFAULT_TICKER_BLACKLIST: Set[str] = {
    "A", "I", "AI", "DD", "CEO", "CFO", "CTO", "COO", "YOLO", "FOMO",
    "ATH", "EOD", "EPS", "ETF", "ETFS", "GDP", "SEC", "FDA", "USA", "USD",
    "FED", "WSB", "RH", "MOASS", "IPO", "SPAC", "SPY", "QQQ", "DIA", "IWM",
    "VIX", "CALL", "PUT", "CALLS", "PUTS", "BUY", "SELL", "HOLD", "THE", "FOR",
    "AND", "NOT", "CAN", "ARE", "HAS", "HAD", "WAS", "NEW", "NOW", "ALL", "ANY",
    "ONE", "OUT", "BIG", "LOW", "TOP", "SEE", "GET", "GOT", "HE", "SHE", "IT",
    "ITS", "ON", "IN", "AT", "BY", "TO", "OF", "UP", "DO", "GO", "NO", "SO",
    "AM", "PM", "OR", "IF", "AS", "BE", "WE", "US", "ME", "MY", "SO", "OH",
    "OK", "TV", "PR", "IR", "EV", "PE", "NAV", "DCA", "ROI", "BULL", "BEAR",
    "PUMP", "DUMP", "MOON", "GAIN", "LOSS", "LOSSES", "GAINS", "ALERT", "NEWS"
}


@dataclass
class Settings:
    # AWS Settings
    stock_mentions_table: str = os.getenv("STOCK_MENTIONS_TABLE", "StockMentions")
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")
    sns_topic_arn: str = os.getenv("SNS_TOPIC_ARN", "")
    dynamodb_endpoint_url: str = os.getenv("DYNAMODB_ENDPOINT_URL", "")

    # Groq Settings
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

    # Reddit Settings
    reddit_client_id: str = os.getenv("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    reddit_user_agent: str = os.getenv("REDDIT_USER_AGENT", "StockScoringEngine/2.0")
    subreddits: List[str] = field(
        default_factory=lambda: os.getenv("SUBREDDITS", "wallstreetbets,stocks,options").split(",")
    )
    post_limit: int = int(os.getenv("POST_LIMIT", "200"))

    # Discord Settings
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")

    # Thresholds
    spike_threshold_percent: float = float(os.getenv("SPIKE_THRESHOLD_PERCENT", "150.0"))
    total_score_threshold: float = float(os.getenv("TOTAL_SCORE_THRESHOLD", "75.0"))

    # Ticker filtering
    ticker_blacklist: Set[str] = field(default_factory=lambda: DEFAULT_TICKER_BLACKLIST.copy())


settings = Settings()

