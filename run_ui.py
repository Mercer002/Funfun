#!/usr/bin/env python3
"""
Interactive UI Server and Backend API for the Multi-Factor Stock Scoring Engine v2.0.
Provides:
  - Static file hosting for the web dashboard (web/index.html)
  - GET  /api/evaluate?ticker=NVDA - Fetches LIVE yfinance metrics & calculates real scores
  - POST /api/run-screen          - Executes live pipeline ingestion & scoring screen
Usage:
    python3 run_ui.py
"""

from concurrent.futures import ThreadPoolExecutor
import http.server
import json
import logging
import os
import socketserver
import sys
from urllib.parse import parse_qs, urlparse
import webbrowser

from src.config import settings
from src.pipeline import PipelineRunner
from src.scoring_engine import MultiFactorScoringEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("UIServer")

def find_available_port(preferred: int = 8088) -> int:
    import socket
    for p in [preferred, 8088, 8089, 8090, 8888, 5001]:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("", p))
                return p
        except OSError:
            continue
    return preferred

PORT = find_available_port(int(os.getenv("PORT", "8088")))
WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class PipelineAPIHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/evaluate":
            self.handle_live_evaluate(parsed.query)
        elif parsed.path == "/api/status":
            self.handle_status()
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/run-screen":
            self.handle_run_screen()
        else:
            self.send_error(404, "Endpoint Not Found")

    def handle_status(self):
        """Returns pipeline configuration and status."""
        status = {
            "status": "online",
            "version": "2.0",
            "spike_threshold_percent": settings.spike_threshold_percent,
            "total_score_threshold": settings.total_score_threshold,
            "groq_model": settings.groq_model,
            "dynamodb_table": settings.stock_mentions_table,
            "aws_region": settings.aws_region
        }
        self.send_json_response(status)

    def handle_live_evaluate(self, query_string: str):
        """
        Fetches LIVE fundamental data for a ticker from yfinance,
        evaluates against the quantitative scoring model, and returns score breakdown.
        """
        params = parse_qs(query_string)
        ticker = params.get("ticker", ["NVDA"])[0].upper().strip()
        catalyst = params.get("catalyst", ["NONE"])[0].upper().strip()
        velocity = float(params.get("velocity", [550.0])[0])
        sentiment = float(params.get("sentiment", [0.85])[0])

        logger.info("Evaluating live ticker: $%s (Catalyst: %s, Velocity: %.1f%%)", ticker, catalyst, velocity)

        engine = MultiFactorScoringEngine()
        scored = engine.score_ticker(
            ticker=ticker,
            velocity_spike_pct=velocity,
            sentiment_polarity=sentiment,
            catalyst=catalyst,
            catalyst_reasoning=f"User evaluation with catalyst '{catalyst}'"
        )

        response = {
            "ticker": scored.ticker,
            "company_name": scored.fundamentals.company_name or scored.ticker,
            "market_cap": scored.fundamentals.market_cap,
            "operating_cash_flow": scored.fundamentals.operating_cash_flow,
            "forward_pe": scored.fundamentals.forward_pe,
            "revenue_growth": scored.fundamentals.revenue_growth,
            "peg_ratio": scored.fundamentals.peg_ratio,
            "fundamental_score": scored.fundamental_score,
            "sentiment_score_points": scored.sentiment_score_points,
            "catalyst_bonus": scored.catalyst_bonus,
            "total_score": scored.total_score,
            "is_rescued_by_catalyst": scored.breakdown.is_rescued_by_catalyst,
            "breakdown": {
                "market_cap_pts": scored.breakdown.market_cap_pts,
                "profitability_pts": scored.breakdown.profitability_pts,
                "valuation_pts": scored.breakdown.valuation_pts,
                "growth_bonus_pts": scored.breakdown.growth_bonus_pts,
                "overvaluation_penalty_pts": scored.breakdown.overvaluation_penalty_pts,
                "velocity_pts": scored.breakdown.velocity_pts,
                "sentiment_pts": scored.breakdown.sentiment_pts,
                "catalyst_bonus_pts": scored.breakdown.catalyst_bonus_pts,
                "breakdown_text": scored.breakdown.breakdown_text
            }
        }
        self.send_json_response(response)

    def handle_run_screen(self):
        """Executes full live pipeline screen across social feeds and yfinance."""
        logger.info("Triggered live pipeline screen via web API...")
        try:
            runner = PipelineRunner()
            result = runner.run(dry_run=True)
            self.send_json_response(result)
        except Exception as exc:
            logger.error("Screen execution error: %s", exc)
            self.send_json_response({"status": "error", "message": str(exc)}, status_code=500)

    def send_json_response(self, data: dict, status_code: int = 200):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def main():
    url = f"http://localhost:{PORT}/index.html"
    server_address = ("", PORT)

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(server_address, PipelineAPIHandler) as httpd:
        print("=" * 70)
        print("  MULTI-FACTOR STOCK SCORING ENGINE v2.0 - LIVE DASHBOARD & API")
        print(f"  • Dashboard UI: {url}")
        print(f"  • Live API:     http://localhost:{PORT}/api/evaluate?ticker=NVDA")
        print(f"  • Screen API:   http://localhost:{PORT}/api/run-screen")
        print("  Press Ctrl+C to stop.")
        print("=" * 70)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            httpd.shutdown()


if __name__ == "__main__":
    main()
