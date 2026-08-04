"""HTTP fetch layer. All network I/O for the scorer lives here.

Scope note (Day 1 / Crawlability): the basic fetch_page()/FetchResult pairing
does the one thing Crawlability needs -- follow redirects, hand back the
final status code, headers, and raw HTML.

Day 2 addition: the full three-tier confidence model from SCOPE_OF_WORK.md
§4 lives here too (Unscored / Scored-low-confidence / Scored-high-confidence,
including both detectors). It's applied to the whole page once, not
per-component, which is why it sits alongside the fetch layer rather than
inside any one component's scoring module -- Content Structure is the first
component that *needs* it (word count, closing-tag well-formedness), but the
assessment itself isn't Content-Structure-specific.
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import requests
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT_SECONDS = 10.0

# --- confidence model (§4) --------------------------------------------------

# Named, documented, explicitly tunable constant -- NOT a decided value.
# Starting point pending recalibration against real customer pages, matching
# the WeekLift threshold-personalization precedent (documented, revisit-
# later, not permanently settled). Tracked in SCOPE_OF_WORK.md §10.
MIN_VISIBLE_TEXT_WORDS = 150

# Reason codes for the two low-confidence detectors. Exact strings are
# locked per SCOPE_OF_WORK.md §4 -- do not rename without updating the SoW.
#
# Governing principle (non-negotiable, applies to all future detectors added
# to this model): reason codes report what was OBSERVED, never what's
# INFERRED. A legitimately sloppy-but-complete page and a truncated one look
# identical to the closing-tag check; a legitimately short page (pricing,
# contact, landing page) looks identical to the word-count check. Neither
# code may claim more than it actually knows.
REASON_CLOSING_HTML_TAG_NOT_FOUND = "closing_html_tag_not_found"
REASON_LOW_VISIBLE_TEXT_WORD_COUNT = "low_visible_text_word_count"

# Reason codes for the Unscored tier. Less rigidly specified by the SoW than
# the two low-confidence detectors above, but kept as named constants rather
# than inline strings for the same reason.
REASON_NO_HTTP_RESPONSE = "no_http_response"
REASON_NON_HTML_CONTENT_TYPE = "non_html_content_type"


class ConfidenceTier(str, Enum):
    """SCOPE_OF_WORK.md §4's three tiers, applied to the whole score."""

    UNSCORED = "unscored"
    SCORED_LOW_CONFIDENCE = "scored_low_confidence"
    SCORED_HIGH_CONFIDENCE = "scored_high_confidence"


@dataclass
class ConfidenceResult:
    """Confidence assessment for a fetched page.

    tier and the eventual numeric score are kept as structurally separate
    fields/objects throughout this codebase (this dataclass never carries a
    score, and no scoring dataclass carries a tier) -- per §4's schema-level
    requirement that a future UI must not be able to accidentally render
    "low confidence" and "bad score" as the same visual signal.

    visible_text_word_count is included unconditionally whenever it's
    computable (i.e. whenever we got well-formed-enough HTML to extract
    text from) -- not just the boolean low-word-count flag -- so the
    MIN_VISIBLE_TEXT_WORDS threshold can be retuned from real evidence later
    rather than guessed at twice (§4).
    """

    tier: ConfidenceTier
    reason_codes: list = field(default_factory=list)  # populated for SCORED_LOW_CONFIDENCE; may hold both detectors at once
    unscored_reason: Optional[str] = None  # populated only when tier is UNSCORED
    visible_text_word_count: Optional[int] = None  # None only when tier is UNSCORED (no usable HTML to count)


def count_visible_words(html: str) -> int:
    """Extract visible text and count words.

    "Visible" means: not inside <script>, <style>, or <noscript>. This is a
    blunt heuristic, not a real rendering pass -- consistent with §5's
    accepted limitation that this tool does not execute JavaScript.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    return len(text.split())


def _closing_html_tag_present(html: str) -> bool:
    """Case-insensitive check for a `</html>` tag anywhere in the raw text.

    Deliberately a raw-text regex, not a parser check -- BeautifulSoup will
    happily "fix up" malformed/incomplete HTML and report success regardless,
    which would defeat the point of this detector.
    """
    return re.search(r"</\s*html\s*>", html, re.IGNORECASE) is not None


def assess_confidence(fetch_result: "FetchResult") -> ConfidenceResult:
    """Assess confidence tier for a fetched page, per §4.

    Tier 1 (Unscored): no HTTP response at all, or a non-HTML content-type.
    Nothing downstream can be evaluated honestly.

    Tier 2/3: page fetched and parsed. Runs both detectors; either or both
    may fire. Low-confidence if either fires, high-confidence if neither
    does.
    """
    if fetch_result.error is not None or fetch_result.html is None:
        return ConfidenceResult(
            tier=ConfidenceTier.UNSCORED,
            unscored_reason=REASON_NO_HTTP_RESPONSE,
        )

    content_type = ""
    for header_name, header_value in fetch_result.headers.items():
        if header_name.lower() == "content-type":
            content_type = header_value or ""
            break
    if content_type and "html" not in content_type.lower():
        return ConfidenceResult(
            tier=ConfidenceTier.UNSCORED,
            unscored_reason=REASON_NON_HTML_CONTENT_TYPE,
        )

    reason_codes = []
    if not _closing_html_tag_present(fetch_result.html):
        reason_codes.append(REASON_CLOSING_HTML_TAG_NOT_FOUND)

    word_count = count_visible_words(fetch_result.html)
    if word_count < MIN_VISIBLE_TEXT_WORDS:
        reason_codes.append(REASON_LOW_VISIBLE_TEXT_WORD_COUNT)

    tier = (
        ConfidenceTier.SCORED_LOW_CONFIDENCE
        if reason_codes
        else ConfidenceTier.SCORED_HIGH_CONFIDENCE
    )

    return ConfidenceResult(
        tier=tier,
        reason_codes=reason_codes,
        visible_text_word_count=word_count,
    )


# --- fetch --------------------------------------------------------------


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
