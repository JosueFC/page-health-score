"""Crawlability scoring (SCOPE_OF_WORK.md §3). 20 points, three signals.

Pure function, no I/O -- takes an already-populated FetchResult and returns a
CrawlabilityResult. Assumes fetch_result.html is not None: reachability /
connection-failure handling is gated upstream, in fetch.py / score.py, not
here. (This was flagged and confirmed during scope review -- §3's prose
mention of "page reachable/fetchable" as a fourth signal was leftover from
before the §4 confidence model existed; it was never a fourth scored point
bucket on top of the confirmed 10/6/4 split.)

Signal breakdown (CONFIRMED, §3):
    - HTTP 200 (final, post-redirect):            10 points
    - No noindex (meta robots OR X-Robots-Tag):     6 points
    - Canonical present and self-referencing:       4 points
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from page_health.fetch import FetchResult

STATUS_POINTS = 10
NOINDEX_POINTS = 6
CANONICAL_POINTS = 4
MAX_POINTS = STATUS_POINTS + NOINDEX_POINTS + CANONICAL_POINTS  # 20

# Canonical outcome reasons. "malformed_*" and "points_elsewhere" both score
# 0/4 (same point outcome) but are kept as distinguishable strings in the
# output per the scope-review decision: a canonical that's broken and a
# canonical that's fine-but-wrong are different fixes for a reader, even
# though the score doesn't tell them apart.
CANONICAL_SELF_REFERENCING = "self_referencing"
CANONICAL_ABSENT = "absent"
CANONICAL_MALFORMED_EMPTY_HREF = "malformed_empty_href"
CANONICAL_MALFORMED_UNRESOLVABLE = "malformed_unresolvable"
CANONICAL_MALFORMED_MULTIPLE = "malformed_multiple_conflicting"
CANONICAL_POINTS_ELSEWHERE = "points_elsewhere"

# Reasons that score 0/4 -- "malformed" and "absent" and "points elsewhere"
# are all treated at least as harshly as no signal (§3).
_ZERO_SCORE_CANONICAL_REASONS = {
    CANONICAL_ABSENT,
    CANONICAL_MALFORMED_EMPTY_HREF,
    CANONICAL_MALFORMED_UNRESOLVABLE,
    CANONICAL_MALFORMED_MULTIPLE,
    CANONICAL_POINTS_ELSEWHERE,
}


@dataclass
class CrawlabilityResult:
    """Score + raw diagnostics for the Crawlability component.

    Raw observed values are included unconditionally (not just booleans),
    per SCOPE_OF_WORK.md §8's output format requirement.
    """

    points: int
    max_points: int

    status_code: Optional[int]
    status_points: int

    noindex: bool
    noindex_source: Optional[str]  # "meta_robots", "x_robots_tag", "meta_robots+x_robots_tag", or None
    noindex_points: int

    canonical_reason: str
    canonical_value: Optional[str]  # raw href attribute value, if any single canonical tag was found
    canonical_resolved: Optional[str]  # canonical_value resolved against final_url, if resolvable
    canonical_points: int


def _normalize_url(url: str) -> str:
    """Normalize a URL for self-reference comparison.

    Strips fragment (irrelevant to canonicalization) and a single trailing
    slash on the path (avoid a false "points elsewhere" for /page vs /page/).
    Scheme and host are lowercased (URLs are case-insensitive there); path is
    left case-sensitive since many servers treat it that way.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _score_status(fetch_result: FetchResult) -> int:
    return STATUS_POINTS if fetch_result.status_code == 200 else 0


def _check_noindex(fetch_result: FetchResult, soup: BeautifulSoup) -> tuple[bool, Optional[str]]:
    """Check both possible noindex signals. Either one present fails this
    signal (§3) -- it's an OR, not an AND.

    v1 default (documented in SCOPE_OF_WORK.md §10): any `noindex` token in
    X-Robots-Tag fails the check, regardless of whether it's scoped to a
    specific user-agent (e.g. "googlebot: noindex") or applies globally.
    """
    sources = []

    meta_tag = soup.find("meta", attrs={"name": lambda v: v and v.lower() == "robots"})
    if meta_tag is not None:
        content = (meta_tag.get("content") or "").lower()
        if "noindex" in [token.strip() for token in content.split(",")]:
            sources.append("meta_robots")

    x_robots_tag = None
    for header_name, header_value in fetch_result.headers.items():
        if header_name.lower() == "x-robots-tag":
            x_robots_tag = header_value
            break
    if x_robots_tag:
        # Value may be e.g. "noindex", "googlebot: noindex", or
        # "noindex, nofollow" -- split on both ":" and "," and check tokens.
        normalized = x_robots_tag.lower().replace(":", ",")
        tokens = [token.strip() for token in normalized.split(",")]
        if "noindex" in tokens:
            sources.append("x_robots_tag")

    if not sources:
        return False, None
    return True, "+".join(sources)


def _check_canonical(fetch_result: FetchResult, soup: BeautifulSoup) -> tuple[str, Optional[str], Optional[str]]:
    """Returns (reason, raw_href_value, resolved_url)."""
    canonical_tags = soup.find_all("link", rel=lambda v: v and "canonical" in [r.lower() for r in (v if isinstance(v, list) else v.split())])

    if len(canonical_tags) == 0:
        return CANONICAL_ABSENT, None, None

    if len(canonical_tags) > 1:
        # Multiple canonical tags is treated as malformed/conflicting
        # regardless of whether they happen to agree -- §3: "a confusing
        # signal is treated at least as harshly as no signal."
        return CANONICAL_MALFORMED_MULTIPLE, None, None

    href = (canonical_tags[0].get("href") or "").strip()
    if not href:
        return CANONICAL_MALFORMED_EMPTY_HREF, href or None, None

    try:
        resolved = urljoin(fetch_result.final_url, href)
        resolved_parts = urlsplit(resolved)
        if not resolved_parts.scheme or not resolved_parts.netloc:
            return CANONICAL_MALFORMED_UNRESOLVABLE, href, None
    except ValueError:
        return CANONICAL_MALFORMED_UNRESOLVABLE, href, None

    if _normalize_url(resolved) == _normalize_url(fetch_result.final_url):
        return CANONICAL_SELF_REFERENCING, href, resolved

    return CANONICAL_POINTS_ELSEWHERE, href, resolved


def score_crawlability(fetch_result: FetchResult) -> CrawlabilityResult:
    """Score the Crawlability component (20 points) for an already-fetched page.

    Assumes fetch_result.html is not None -- callers must gate unreachable /
    connection-failed pages before calling this (they belong to §4's
    Unscored tier, not a Crawlability point deduction).
    """
    if fetch_result.html is None:
        raise ValueError(
            "score_crawlability() requires fetch_result.html to be present. "
            "Unreachable pages must be gated upstream (fetch.py / score.py), "
            "not scored here -- see SCOPE_OF_WORK.md §4."
        )

    soup = BeautifulSoup(fetch_result.html, "html.parser")

    status_points = _score_status(fetch_result)

    noindex, noindex_source = _check_noindex(fetch_result, soup)
    noindex_points = 0 if noindex else NOINDEX_POINTS

    canonical_reason, canonical_value, canonical_resolved = _check_canonical(fetch_result, soup)
    canonical_points = 0 if canonical_reason in _ZERO_SCORE_CANONICAL_REASONS else CANONICAL_POINTS

    total_points = status_points + noindex_points + canonical_points

    return CrawlabilityResult(
        points=total_points,
        max_points=MAX_POINTS,
        status_code=fetch_result.status_code,
        status_points=status_points,
        noindex=noindex,
        noindex_source=noindex_source,
        noindex_points=noindex_points,
        canonical_reason=canonical_reason,
        canonical_value=canonical_value,
        canonical_resolved=canonical_resolved,
        canonical_points=canonical_points,
    )
