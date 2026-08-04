"""HTTP fetch layer. All network I/O for the scorer lives here.

Scope note (Day 1 / Crawlability): this is intentionally minimal. It does the
one thing Crawlability needs -- follow redirects, hand back the final status
code, headers, and raw HTML -- and nothing more.

It deliberately does NOT implement the three-tier confidence model from
SCOPE_OF_WORK.md §4 (Unscored / low-confidence / high-confidence, including
the closing_html_tag_not_found and low_visible_text_word_count detectors).
That model is deferred to the Content Structure component (Day 2), per the
agreed build order in §7 -- Crawlability's three signals don't need it.

Because that model isn't here yet, `error` on FetchResult is currently the
only failure signal (connection errors, timeouts, DNS failures, etc.). A
non-HTML content-type or a malformed response body is NOT yet distinguished
from a normal successful fetch -- that distinction belongs to §4 and arrives
on Day 2.
"""

from dataclasses import dataclass, field
from typing import Optional

import requests

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass
class FetchResult:
    """Result of attempting to fetch a single URL.

    original_url and final_url are recorded separately (not just the final
    one) so redirect chains are visible in output, per SCOPE_OF_WORK.md §3's
    "Original vs. final URL recorded in output as context" requirement and
    §8's output format.
    """

    original_url: str
    final_url: str
    status_code: Optional[int]
    headers: dict = field(default_factory=dict)
    html: Optional[str] = None
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """True if we got any HTTP response at all (regardless of status code).

        A 404 or 500 still "succeeded" at the transport level -- the page
        responded, it just responded with a failure status. That distinction
        matters to Crawlability's scoring but not to whether fetch.py did its
        job. True connection-level failure (no response at all) is what
        `error` captures.
        """
        return self.error is None and self.html is not None


def fetch_page(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> FetchResult:
    """Fetch a URL, following redirects, and return the raw result.

    No parsing, no scoring, no confidence judgments -- just the HTTP call.
    Callers (crawlability.py, and later score.py) decide what to do with the
    result.
    """
    try:
        response = requests.get(url, timeout=timeout, allow_redirects=True)
    except requests.RequestException as exc:
        return FetchResult(
            original_url=url,
            final_url=url,
            status_code=None,
            headers={},
            html=None,
            error=str(exc),
        )

    return FetchResult(
        original_url=url,
        final_url=response.url,
        status_code=response.status_code,
        headers=dict(response.headers),
        html=response.text,
        error=None,
    )
