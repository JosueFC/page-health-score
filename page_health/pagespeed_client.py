"""PageSpeed Insights client. All I/O for Technical Quality's external
dependency lives here -- mirrors fetch.py's I/O-isolation contract.
technical.py must stay a pure function; retry/backoff/throttle logic belongs
entirely in this module and must never leak into the scoring layer (Day 4
sign-off, structural boundary restated explicitly).

Rate-limit strategy (§5, signed off Day 4 -- option B):
    - Self-throttle to stay under PSI's informal ~1 req/sec unauthenticated
      limit.
    - Retry exactly once on HTTP 429 -- DEFAULT_MAX_ATTEMPTS = 2, meaning one
      retry after the initial attempt. This mirrors the retry convention
      already established in weeklift's resend_client.py
      (DEFAULT_MAX_ATTEMPTS = 2), so if this project is ever folded back
      toward that codebase, the retry philosophy already matches. Not an
      open-ended "one or two" -- exactly one.
    - Any failure that survives the retry (or any non-429 failure, which
      isn't retried at all) surfaces PSI's actual status code and response
      detail as the error. Never a generic "gave up after retries" message
      -- a persistent problem (bad API key, quota exhausted, genuine
      outage) needs to be loud and specific, not masked behind a retry loop.
    - Connection-level failures (DNS, timeout, refused connection) are not
      retried -- only HTTP 429 responses are. A dead network doesn't get
      better on a second try within the same call.
"""

import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

PSI_ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# PSI's informally-enforced unauthenticated rate limit is roughly 1 req/sec
# (§5) -- stay under it with a small margin.
MIN_SECONDS_BETWEEN_CALLS = 1.1

# One retry after the initial attempt -- see module docstring.
DEFAULT_MAX_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 2.0

_last_call_time: Optional[float] = None


@dataclass
class PageSpeedResult:
    """Result of a PageSpeed Insights request.

    error is None on success. On failure it always carries PSI's actual
    status code and/or response detail -- see module docstring on why a
    generic "retries exhausted" message is deliberately avoided.
    """

    seo_score: Optional[int]  # 0-100, PSI's own SEO category score
    performance_score: Optional[int]  # 0-100, PSI's own performance category score
    error: Optional[str]
    status_code: Optional[int]


def _throttle() -> None:
    """Sleep just enough to stay under MIN_SECONDS_BETWEEN_CALLS since the
    last call made by this process. No-op on the first call."""
    global _last_call_time
    if _last_call_time is not None:
        elapsed = time.monotonic() - _last_call_time
        remaining = MIN_SECONDS_BETWEEN_CALLS - elapsed
        if remaining > 0:
            time.sleep(remaining)
    _last_call_time = time.monotonic()


def _request_once(url: str, api_key: Optional[str], timeout: float) -> requests.Response:
    params = {"url": url, "category": ["SEO", "PERFORMANCE"]}
    if api_key:
        params["key"] = api_key
    _throttle()
    return requests.get(PSI_ENDPOINT, params=params, timeout=timeout)


def fetch_pagespeed_data(
    url: str, api_key: Optional[str] = None, timeout: float = 30.0
) -> PageSpeedResult:
    """Fetch SEO + performance category scores from PageSpeed Insights.

    api_key: if not passed explicitly, falls back to the PAGESPEED_API_KEY
    environment variable, then to unauthenticated (lower daily quota, per
    §5). No key is required to run this tool at all, consistent with §9's
    "no auth" scope.
    """
    if api_key is None:
        api_key = os.environ.get("PAGESPEED_API_KEY")

    last_response: Optional[requests.Response] = None
    connection_error: Optional[Exception] = None

    for attempt in range(DEFAULT_MAX_ATTEMPTS):
        try:
            response = _request_once(url, api_key, timeout)
        except requests.RequestException as exc:
            connection_error = exc
            break  # connection-level failures are not retried

        last_response = response
        if response.status_code == 429 and attempt < DEFAULT_MAX_ATTEMPTS - 1:
            time.sleep(RETRY_BACKOFF_SECONDS)
            continue
        break

    if connection_error is not None:
        return PageSpeedResult(
            seo_score=None,
            performance_score=None,
            error=str(connection_error),
            status_code=None,
        )

    if last_response.status_code != 200:
        return PageSpeedResult(
            seo_score=None,
            performance_score=None,
            error=(
                f"PageSpeed Insights returned HTTP {last_response.status_code}: "
                f"{last_response.text[:500]}"
            ),
            status_code=last_response.status_code,
        )

    try:
        data = last_response.json()
        categories = data["lighthouseResult"]["categories"]
        seo_score = round(categories["seo"]["score"] * 100)
        performance_score = round(categories["performance"]["score"] * 100)
    except (KeyError, ValueError, TypeError) as exc:
        return PageSpeedResult(
            seo_score=None,
            performance_score=None,
            error=f"PageSpeed Insights returned an unexpected response shape: {exc}",
            status_code=last_response.status_code,
        )

    return PageSpeedResult(
        seo_score=seo_score,
        performance_score=performance_score,
        error=None,
        status_code=200,
    )
