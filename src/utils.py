"""
Utility functions for retries, backoff, and thread concurrency.
"""

from functools import wraps
import logging
import time
from typing import Any, Callable, Type, Tuple

logger = logging.getLogger(__name__)


def retry_with_backoff(
    retries: int = 3,
    backoff_in_seconds: float = 1.0,
    max_backoff_in_seconds: float = 8.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
) -> Callable:
    """
    Decorator for retrying functions with exponential backoff and jitter.
    Useful for API calls to Groq, yfinance, and Reddit.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            current_delay = backoff_in_seconds
            while True:
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    attempt += 1
                    if attempt > retries:
                        logger.error(
                            "Function '%s' failed after %d retries. Exception: %s",
                            func.__name__,
                            retries,
                            exc
                        )
                        raise
                    logger.warning(
                        "Function '%s' raised %s. Retrying attempt %d/%d in %.1fs...",
                        func.__name__,
                        exc.__class__.__name__,
                        attempt,
                        retries,
                        current_delay
                    )
                    time.sleep(current_delay)
                    current_delay = min(current_delay * 2.0, max_backoff_in_seconds)
        return wrapper
    return decorator

