"""Retry-транспорт для внешних уведомлений."""

import re
import time
from typing import Callable

import requests
from loguru import logger

RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def _sanitize_request_error(error: Exception) -> str:
    text = str(error)
    text = re.sub(r"/bot[^/\s]+/", "/bot<hidden>/", text)
    text = re.sub(r"([?&](?:access_token|token|key)=)[^&\s]+", r"\1<hidden>", text, flags=re.I)
    return text


def send_with_retries(
    send_fn: Callable[[], requests.Response],
    *,
    retries: int = 5,
    delay: float = 2.0,
    backoff: float = 1.5,
):
    """
    Универсальный retry для отправки уведомлений
    """

    for attempt in range(1, retries + 1):
        try:
            response = send_fn()

            if response.status_code in RETRY_STATUS_CODES:
                raise requests.HTTPError(
                    f"Retryable status {response.status_code}",
                    response=response,
                )

            response.raise_for_status()
            return response

        except requests.RequestException as err:
            logger.warning(
                f"[notify retry] attempt {attempt}/{retries}: "
                f"{err.__class__.__name__}: {_sanitize_request_error(err)}"
            )

            if attempt >= retries:
                logger.error("[notify retry] retries exhausted")
                raise

            sleep_time = delay * (backoff ** (attempt - 1))
            time.sleep(sleep_time)
