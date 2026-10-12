"""
AWS Lambda Handler for EventBridge Scheduled Pipeline Execution.
Invoked after market close to evaluate social momentum, Groq catalysts,
and fundamental multi-factor scores.
"""

import json
import logging
from typing import Any, Dict

from src.pipeline import PipelineRunner

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Standard AWS Lambda entry point.
    Handles scheduled EventBridge triggers or test event payloads.
    """
    logger.info("Lambda invoked with event: %s", json.dumps(event))

    dry_run = event.get("dry_run", False) if isinstance(event, dict) else False

    runner = PipelineRunner()
    result = runner.run(dry_run=dry_run)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(result, default=str)
    }

