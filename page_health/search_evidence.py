"""Search Evidence scoring (SCOPE_OF_WORK.md §3). 15 points, three cascaded
signals, whole component rescales to 0 when GSC is unavailable.

Pure function, no I/O -- takes an already-fetched GSCResult (from
gsc_client.fetch_search_console_data) and returns a SearchEvidenceResult.
Unlike every other component, this one takes no FetchResult at all -- it has
nothing to do with the page's HTML, only with GSC's own data about it.

Point breakdown (Day 5 sign-off, 5/5/5 split -- tunable, §10):
    - Impressions:      5 points, gate at >= IMPRESSIONS_THRESHOLD
    - CTR:               5 points, gated BY the impressions signal (below)
    - Query diversity:   5 points, gated BY the impressions signal (below)

Decision A (component-level rescale, not score-0): if GSC is unavailable --
no credentials configured, no site URL configured, auth failure, or the API
call itself fails -- this ENTIRE 15-point component rescales out of the
page's denominator, rather than scoring 0. This is the same
Unscored-at-component-granularity mechanism used for Technical Quality's
PSI-failure case (§4 applied at component instead of page granularity), but
with an even cleaner trigger: a missing credential file is unambiguous,
unlike a PSI 429 which could be a transient blip or a real problem. No GSC
access isn't "we measured this page's search traction and it's poor" --
it's "we have no channel to measure it at all." Scoring 0 would assert a
fact that was never established.

Per §11, GSC access is the DEFAULT missing state for every page scored
before WeekLift integration exists -- this isn't a rare edge case for this
component, it's the common case. That's precisely why the final displayed
score is always PROJECTED onto a 0-100 scale in score.py, rather than shown
as a raw variable-denominator fraction -- see score.py's module docstring
for the full reasoning (Decision C).

Decision B (cascade, not a second invented threshold): CTR and query
diversity are gated by the IMPRESSIONS signal's own threshold, not by a
separately-invented "minimum impressions floor." A page that fails the
impressions gate has, by definition, too little traffic for CTR or query
diversity to mean anything (a 100% CTR on one impression is noise, not
signal) -- so failing Impressions gates CTR and query diversity to zero,
exactly the same cascade pattern already used in structured_data.py
(JSON-LD-present gates parsing gates type-specificity gates
required-properties). One number doing double duty, not two thresholds to
separately justify and tune.
"""

from dataclasses import dataclass
from typing import Optional

from page_health.gsc_client import GSCResult

IMPRESSIONS_POINTS = 5
CTR_POINTS = 5
QUERY_DIVERSITY_POINTS = 5
MAX_POINTS = IMPRESSIONS_POINTS + CTR_POINTS + QUERY_DIVERSITY_POINTS  # 15

# All three thresholds below are unresearched starting points -- explicitly
# tunable, tracked in §10, same status as MIN_VISIBLE_TEXT_WORDS and
# MIN_INTERNAL_LINKS pending real customer data.
IMPRESSIONS_THRESHOLD = 100  # over the 90-day window (gsc_client.WINDOW_DAYS)
CTR_THRESHOLD = 0.02  # 2%
QUERY_DIVERSITY_THRESHOLD = 5  # distinct queries


@dataclass
class SearchEvidenceResult:
    """Score + raw diagnostics for the Search Evidence component.

    max_points is 15 when GSC data was retrieved, 0 when it wasn't
    (component-level rescale -- Decision A). Callers combining sub-scores
    (score.py) must read max_points per-result, same requirement already
    established for StructuredDataResult and TechnicalQualityResult.

    Raw impressions/CTR/query-count are included whenever GSC data was
    retrieved, even when the impressions gate fails and CTR/diversity are
    consequently gated to zero -- so a reader can see e.g. "33% CTR on 3
    impressions" for what it actually is, rather than the scoring logic
    silently absorbing that context.
    """

    points: int
    max_points: int

    gsc_available: bool
    gsc_error: Optional[str]

    impressions: Optional[int]
    impressions_points: int

    ctr: Optional[float]
    ctr_points: int

    distinct_query_count: Optional[int]
    query_diversity_points: int


def score_search_evidence(gsc_result: GSCResult) -> SearchEvidenceResult:
    """Score the Search Evidence component (15 points, or rescaled to 0/0
    when GSC is unavailable)."""
    gsc_available = gsc_result.error is None

    if not gsc_available:
        return SearchEvidenceResult(
            points=0,
            max_points=0,  # Decision A: rescaled out entirely, not scored 0/15
            gsc_available=False,
            gsc_error=gsc_result.error,
            impressions=None,
            impressions_points=0,
            ctr=None,
            ctr_points=0,
            distinct_query_count=None,
            query_diversity_points=0,
        )

    impressions_points = (
        IMPRESSIONS_POINTS if (gsc_result.impressions or 0) >= IMPRESSIONS_THRESHOLD else 0
    )

    # Decision B: CTR and query diversity are gated by the impressions
    # signal, not an independently-invented threshold.
    if impressions_points > 0:
        ctr_points = CTR_POINTS if (gsc_result.ctr or 0.0) >= CTR_THRESHOLD else 0
        query_diversity_points = (
            QUERY_DIVERSITY_POINTS
            if (gsc_result.distinct_query_count or 0) >= QUERY_DIVERSITY_THRESHOLD
            else 0
        )
    else:
        ctr_points = 0
        query_diversity_points = 0

    total_points = impressions_points + ctr_points + query_diversity_points

    return SearchEvidenceResult(
        points=total_points,
        max_points=MAX_POINTS,
        gsc_available=True,
        gsc_error=None,
        impressions=gsc_result.impressions,
        impressions_points=impressions_points,
        ctr=gsc_result.ctr,
        ctr_points=ctr_points,
        distinct_query_count=gsc_result.distinct_query_count,
        query_diversity_points=query_diversity_points,
    )
