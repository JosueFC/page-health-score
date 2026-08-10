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

Post-Day-6 addition: fetch identity (User-Agent + Accept headers). Found by
testing this tool against a real site (rcimportspr.com) that returned HTTP
406 under requests' default "python-requests/x.x" User-Agent but 200 under
a real browser UA -- a WAF fingerprinting the client, not a genuine page
problem. Left as the default, Crawlability would have reported "this tool
got blocked" as if it meant "a search crawler would get blocked," which
isn't the same claim.

Decision (confirmed): USER_AGENT is configurable via env var, mirroring the
PAGESPEED_API_KEY / GSC_CREDENTIALS_PATH pattern already established
elsewhere -- sensible default, documented, operator-overridable -- rather
than hardcoded with no override (inconsistent with every other
externally-facing default in this project) or an honest custom identity
like "PageHealthScoreBot/1.0" (empirically the wrong direction: most WAFs
default-deny unrecognized bots, so this would likely get blocked MORE
often, reproducing the exact failure being fixed). Real Googlebot
impersonation was considered and rejected as the DEFAULT -- that's an
ethics question ("should this tool impersonate Google") a default
shouldn't quietly answer -- but making the UA configurable necessarily
reopens that as an operator opt-in override, which is accepted with two
required safeguards:
    1. The UA actually used on every request is always recorded on
       FetchResult.request_user_agent -- never just used internally and
       discarded. Same "always report what was actually observed"
       discipline as PSI error detail and Structured Data's broken-block
       counts.
    2. See SCOPE_OF_WORK.md §5: overriding to a Googlebot-mimicking UA is
       the operator's own choice, outside this tool's default judgment --
       not a supported or endorsed mode, just an override the design
       doesn't prevent.

Accept / Accept-Language are bundled alongside whichever UA is active,
hardcoded rather than separately configurable: a request with only
User-Agent populated and no other normal browser headers is itself an
unusual fingerprint some WAFs specifically flag -- a partial disguise can
read as MORE suspicious than no disguise, same lesson as Structured Data's
"one broken schema block is worse than none." No evidence yet that
operators need to control these independently of the UA, so no separate
env vars for them (would be solving a problem nobody has).

Explicitly deferred, not part of this fix: detecting and labeling "blocked
by bot detection" as distinct from a genuine 404/500 (recognizing
Cloudflare/Akamai challenge-page shapes, etc.). Same reasoning as rejecting
SPA-framework-fingerprinting in §5 -- signature-matching a WAF's challenge
page is a fragile, ongoing arms race against every vendor's changing
templates, for uncertain value. This fix addresses getting real failures
instead of false ones; precisely labeling the failure type is a separate,
lower-value, higher-fragility project.
"""

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import requests
from bs4 import BeautifulSoup

DEFAULT_TIMEOUT_SECONDS = 10.0

# --- fetch identity ----------------------------------------------------

# A real, current browser UA string -- NOT requests' default identity, and
# NOT a Googlebot impersonation (that's an operator opt-in via USER_AGENT,
# not the default -- see module docstring). This specific string is itself
# a §10 tunable: browser UA strings age as new versions ship, and this one
# will eventually look stale and may need periodic updating the same way
# any dated value does.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Hardcoded, bundled with whichever UA is active -- see module docstring on
# why these aren't independently configurable.
DEFAULT_ACCEPT_HEADER = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
DEFAULT_ACCEPT_LANGUAGE_HEADER = "en-US,en;q=0.9"


def _resolve_request_headers() -> dict:
    """Builds the header set for an outgoing fetch. UA is operator-
    overridable via USER_AGENT; Accept/Accept-Language are always the
    hardcoded defaults regardless of UA override (a mismatched pair is an
    accepted, documented v1 simplification -- see SCOPE_OF_WORK.md §5)."""
    user_agent = os.environ.get("USER_AGENT") or DEFAULT_USER_AGENT
    return {
        "User-Agent": user_agent,
        "Accept": DEFAULT_ACCEPT_HEADER,
        "Accept-Language": DEFAULT_ACCEPT_LANGUAGE_HEADER,
    }


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
#
# invalid_url vs. no_http_response was a real precision bug, not a
# hypothetical one: a scheme-less URL (e.g. "weeklift.com" instead of
# "https://weeklift.com") never gets far enough to attempt a connection at
# all, but was originally collapsed into the same "no_http_response" code as
# a genuine connection failure (DNS failure, refused connection, timeout).
# Those are different facts -- "the input was malformed" vs. "we tried to
# reach it and couldn't" -- and collapsing them violates this module's own
# governing principle (reason codes report what was observed, precisely).
# Caught by manually running the CLI against a real scheme-less URL, not by
# any of the 153 existing tests.
REASON_NO_HTTP_RESPONSE = "no_http_response"
REASON_NON_HTML_CONTENT_TYPE = "non_html_content_type"
REASON_INVALID_URL = "invalid_url"

# Internal categorization on FetchResult.error_category, used by
# assess_confidence() to pick the right Unscored reason. Not part of the
# public reason-code vocabulary itself -- just how fetch_page() tells
# assess_confidence() which kind of failure this was.
_ERROR_CATEGORY_INVALID_URL = "invalid_url"
_ERROR_CATEGORY_CONNECTION_FAILED = "connection_failed"


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
        reason = (
            REASON_INVALID_URL
            if fetch_result.error_category == _ERROR_CATEGORY_INVALID_URL
            else REASON_NO_HTTP_RESPONSE
        )
        return ConfidenceResult(
            tier=ConfidenceTier.UNSCORED,
            unscored_reason=reason,
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
    error_category: Optional[str] = None  # None on success; "invalid_url" or "connection_failed" on failure -- see assess_confidence()
    request_user_agent: str = ""  # the UA actually used for this request -- always populated, per the safeguard on the fetch-identity decision (module docstring)

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

    Sends a browser-like User-Agent (operator-overridable via USER_AGENT)
    plus hardcoded Accept/Accept-Language headers -- see module docstring
    for why. The UA actually used is always recorded on the returned
    FetchResult, on every path including failures, per the safeguard
    attached to that decision.

    A malformed URL is distinguished from a genuine connection failure via
    error_category. requests raises MissingSchema/InvalidSchema/InvalidURL
    before ever attempting a connection, for a URL that's wrong on its
    face -- a different fact from "we tried to reach it and couldn't."
    These are caught via ValueError rather than named one at a time: every
    one of requests' URL-parsing exceptions subclasses both
    RequestException and ValueError, while genuine connection failures
    (ConnectionError, Timeout, etc.) subclass RequestException but NOT
    ValueError -- a clean, exhaustive discriminator rather than an
    enumeration that has to be kept in sync with requests' exception
    hierarchy by hand. (The first version of this fix only caught
    MissingSchema and InvalidURL by name and missed InvalidSchema --
    caught immediately by testing a third malformed-URL shape, not
    guessed correctly the first time.)
    """
    request_headers = _resolve_request_headers()
    user_agent = request_headers["User-Agent"]

    try:
        response = requests.get(url, headers=request_headers, timeout=timeout, allow_redirects=True)
    except ValueError as exc:
        # requests' own URL-parsing failures (MissingSchema, InvalidSchema,
        # InvalidURL) all land here -- input was invalid before any
        # connection was attempted.
        return FetchResult(
            original_url=url,
            final_url=url,
            status_code=None,
            headers={},
            html=None,
            error=str(exc),
            error_category=_ERROR_CATEGORY_INVALID_URL,
            request_user_agent=user_agent,
        )
    except requests.RequestException as exc:
        return FetchResult(
            original_url=url,
            final_url=url,
            status_code=None,
            headers={},
            html=None,
            error=str(exc),
            error_category=_ERROR_CATEGORY_CONNECTION_FAILED,
            request_user_agent=user_agent,
        )

    return FetchResult(
        original_url=url,
        final_url=response.url,
        status_code=response.status_code,
        headers=dict(response.headers),
        html=response.text,
        error=None,
        request_user_agent=user_agent,
    )