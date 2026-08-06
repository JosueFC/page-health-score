"""Technical Quality scoring (SCOPE_OF_WORK.md §3). 20 points, four signals.

Pure function, no I/O -- takes an already-populated FetchResult and an
already-fetched PageSpeedResult (from pagespeed_client.fetch_pagespeed_data)
and returns a TechnicalQualityResult. Assumes fetch_result.html is not None,
same contract as every other component so far. All PSI I/O, throttling, and
retry logic lives in pagespeed_client.py -- this module never calls it
directly, matching the I/O-isolation contract every component has held to.

Point breakdown (Day 4 sign-off, 5/5/5/5 split -- tunable, §10):
    - PSI SEO score:            5 points, binary gate at >= 90
    - PSI performance score:    5 points, binary gate at >= 90
    - Image alt coverage:       5 points, binary gate at >= 90% coverage
    - Internal linking:         5 points, binary gate at >= 3 internal links

§3 frames the PSI-derived signals as "a floor/gate, not a graded quality
signal" -- deliberately not `points = psi_score / 100 * 5`, which would
treat an 85 and a 40 as meaningfully different and drift toward exactly the
fine-grained quality judgment this component isn't making. Day 4 sign-off
chose a binary gate over a three-tier one for the same reason: one line, not
two, is the cleaner read of "gate."

Note on PSI unavailability (resolved, see DECISIONS_LOG.md decision #15): if
pagespeed_result.error is set, the two PSI-derived signals are rescaled OUT
of the denominator as a single 10-point block, rather than scored 0. This is
NOT the same treatment as Structured Data's unknown-type rescale, even
though the mechanism looks identical -- the reasoning is different and
worth keeping straight:

    - Structured Data's unknown-type gap is PERMANENT and STRUCTURAL: the
      tool will never recognize every schema.org type, so "unverifiable" is
      baked into the tool's design.
    - A PSI failure is a TRANSIENT, EXTERNAL event -- closer in spirit to
      fetch.py's own Unscored tier (§4) than to a coverage limitation. This
      is that same concept applied at component granularity instead of page
      granularity: the whole-page fetch either succeeds or the page is
      Unscored entirely; this is the PSI-dependent half of one component
      failing while Crawlability, Content Structure, and Structured Data
      remain fully computable.

Scoring 0 here would assert a fact ("this page fails PSI's quality bar")
that was never actually established -- exactly the kind of overclaim §2/§4
already rule out. Both PSI signals rescale TOGETHER as one block, not
independently, since they come from a single API call -- a failure there is
one event, not two separate unverifiable signals. Alt coverage and internal
linking don't depend on PSI and are unaffected -- they still score normally
and still count toward the page's max_points.

The raw PSI error string is always carried in output (psi_error), even
though it doesn't change the score -- same "diagnostic reported
unconditionally" pattern used for Structured Data's broken-JSON-LD-block
count. This matters more here than it might seem: PSI can fail for
reasons that aren't neutral (the page itself times out PSI's crawler, or is
too heavy to analyze) as well as reasons that clearly are (bad API key,
quota exhausted). The scoring treatment doesn't distinguish between them --
still rescale, never guess a number -- but a reader (or a future
required-properties-style refinement) can tell them apart from the actual
error text.
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from page_health.fetch import FetchResult
from page_health.pagespeed_client import PageSpeedResult

PSI_SEO_POINTS = 5
PSI_PERFORMANCE_POINTS = 5
ALT_COVERAGE_POINTS = 5
INTERNAL_LINKING_POINTS = 5
MAX_POINTS = PSI_SEO_POINTS + PSI_PERFORMANCE_POINTS + ALT_COVERAGE_POINTS + INTERNAL_LINKING_POINTS  # 20

# The two PSI-derived signals rescale out of the denominator TOGETHER, as
# one block, when PSI is unavailable -- they come from a single API call,
# so a failure there is one event, not two independently-unverifiable ones.
PSI_BLOCK_POINTS = PSI_SEO_POINTS + PSI_PERFORMANCE_POINTS  # 10

# Binary gate threshold for both PSI category scores (Day 4 decision #1).
PSI_SCORE_THRESHOLD = 90

# Binary gate threshold for alt-text coverage.
ALT_COVERAGE_THRESHOLD = 0.90

# Minimum internal links to score full points (Day 4 decision #3). Picked as
# a low, unresearched bar -- explicitly tunable, tracked in §10, revisit
# once real customer pages can validate it.
MIN_INTERNAL_LINKS = 3

_NON_NAVIGATIONAL_HREF_PREFIXES = ("#", "mailto:", "tel:", "javascript:")


@dataclass
class TechnicalQualityResult:
    """Score + raw diagnostics for the Technical Quality component.

    max_points is normally 20, but drops to 10 when PSI is unavailable
    (pagespeed_result.error is set) -- the two PSI-derived signals rescale
    out of the denominator together rather than scoring 0. Callers combining
    sub-scores (score.py, Day 5) must read max_points per-result rather than
    assuming a constant 20, same requirement already noted on
    StructuredDataResult.
    """

    points: int
    max_points: int

    psi_available: bool
    psi_error: Optional[str]

    psi_seo_score: Optional[int]  # raw PSI score, always included when available
    psi_seo_points: int

    psi_performance_score: Optional[int]
    psi_performance_points: int

    total_images: int
    images_with_alt: int
    alt_coverage: Optional[float]  # None only when total_images == 0
    alt_points: int

    internal_link_count: int
    internal_link_points: int


def _score_psi_gate(score: Optional[int], available: bool) -> int:
    if not available or score is None:
        return 0
    return PSI_SEO_POINTS if score >= PSI_SCORE_THRESHOLD else 0


def _score_alt_coverage(soup: BeautifulSoup) -> tuple:
    images = soup.find_all("img")
    total_images = len(images)

    if total_images == 0:
        # Day 4 decision #2: a page with no images passes outright -- absence
        # of images isn't an accessibility/SEO problem to demonstrate
        # discipline on.
        return total_images, 0, None, ALT_COVERAGE_POINTS

    images_with_alt = sum(1 for img in images if (img.get("alt") or "").strip())
    coverage = images_with_alt / total_images
    points = ALT_COVERAGE_POINTS if coverage >= ALT_COVERAGE_THRESHOLD else 0
    return total_images, images_with_alt, coverage, points


def _is_internal_link(href: str, base_url: str, base_netloc: str) -> bool:
    href = href.strip()
    if not href:
        return False
    if href.lower().startswith(_NON_NAVIGATIONAL_HREF_PREFIXES):
        return False
    resolved = urljoin(base_url, href)
    return urlsplit(resolved).netloc.lower() == base_netloc


def _score_internal_linking(soup: BeautifulSoup, final_url: str) -> tuple:
    base_netloc = urlsplit(final_url).netloc.lower()
    count = sum(
        1
        for tag in soup.find_all("a", href=True)
        if _is_internal_link(tag["href"], final_url, base_netloc)
    )
    points = INTERNAL_LINKING_POINTS if count >= MIN_INTERNAL_LINKS else 0
    return count, points


def score_technical_quality(
    fetch_result: FetchResult, pagespeed_result: PageSpeedResult
) -> TechnicalQualityResult:
    """Score the Technical Quality component (20 points).

    Assumes fetch_result.html is not None -- same upstream-gating contract
    as the other pure scoring functions. pagespeed_result is passed in
    already-fetched; this function never calls pagespeed_client itself.
    """
    if fetch_result.html is None:
        raise ValueError(
            "score_technical_quality() requires fetch_result.html to be "
            "present. Unreachable pages must be gated upstream, not scored "
            "here -- see SCOPE_OF_WORK.md §4."
        )

    soup = BeautifulSoup(fetch_result.html, "html.parser")

    psi_available = pagespeed_result.error is None
    psi_seo_points = _score_psi_gate(pagespeed_result.seo_score, psi_available)
    psi_performance_points = _score_psi_gate(pagespeed_result.performance_score, psi_available)

    total_images, images_with_alt, alt_coverage, alt_points = _score_alt_coverage(soup)
    internal_link_count, internal_link_points = _score_internal_linking(soup, fetch_result.final_url)

    if psi_available:
        max_points = MAX_POINTS
        total_points = psi_seo_points + psi_performance_points + alt_points + internal_link_points
    else:
        # Rescale: PSI's 10-point block is excluded from the denominator
        # entirely, not scored 0 -- see module docstring, decision #15.
        # psi_seo_points/psi_performance_points are already 0 from
        # _score_psi_gate() above and are not added into the total.
        max_points = MAX_POINTS - PSI_BLOCK_POINTS
        total_points = alt_points + internal_link_points

    return TechnicalQualityResult(
        points=total_points,
        max_points=max_points,
        psi_available=psi_available,
        psi_error=pagespeed_result.error,
        psi_seo_score=pagespeed_result.seo_score,
        psi_seo_points=psi_seo_points,
        psi_performance_score=pagespeed_result.performance_score,
        psi_performance_points=psi_performance_points,
        total_images=total_images,
        images_with_alt=images_with_alt,
        alt_coverage=alt_coverage,
        alt_points=alt_points,
        internal_link_count=internal_link_count,
        internal_link_points=internal_link_points,
    )
