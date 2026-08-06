"""Combines all five components into a single page score (SCOPE_OF_WORK.md
§8). This is the one file that does orchestration -- calling fetch.py,
pagespeed_client.py, and gsc_client.py, then handing their results to each
component's pure scoring function -- so callers (cli.py, eventually
WeekLift's own integration per §11) have one entry point.

Decision C (Day 5 sign-off): the headline score is always PROJECTED onto a
0-100 scale -- score = round(achieved_points / achievable_max_points * 100)
-- rather than shown as a raw variable-denominator fraction like "72/85".

This isn't only about Search Evidence being unavailable. The reserved
10-point buffer (§2) is PERMANENTLY unbuilt and unscored in v1 -- so the
achievable max is at most 90 out of the SoW's own nominal 100, on every
single page, always. Projection is therefore the standard path, not a
special case invoked only when a component rescales out. §1 calls this "a
deterministic, rules-based 0-100 score" -- projection is what keeps that
literally true for every page, including ones where Structured Data hit an
unknown type, Technical Quality lost PSI, or Search Evidence has no GSC
access at all (the default state for every page during the entire
pre-integration period per §11).

The trade-off accepted here, stated once rather than left implicit:
projecting a page's achieved fraction onto 100 is a small claim of
equivalence -- it treats the excluded points as if they'd have scored
average, which isn't strictly established. The alternative (a raw fraction
like "72/85") is more literally honest about what was and wasn't measured,
but breaks the tool's own 0-100 framing for what will be most pages during
this project's actual usable life so far. Day 5 sign-off chose projection;
the raw achieved/achievable numbers are still carried in full in the output
(score_breakdown) for anyone who wants the unprojected picture.

Output schema note (§8): score and confidence tier are kept as
structurally separate fields on PageScoreResult -- confidence never
contributes to the numeric score, and the score is never used to infer a
confidence tier. This mirrors the same separation already enforced between
fetch.ConfidenceResult and every component's *Result dataclass.
"""

from dataclasses import dataclass, field
from typing import Optional

from page_health.content import score_content_structure
from page_health.crawlability import score_crawlability
from page_health.fetch import ConfidenceTier, assess_confidence, fetch_page
from page_health.gsc_client import fetch_search_console_data
from page_health.pagespeed_client import fetch_pagespeed_data
from page_health.search_evidence import score_search_evidence
from page_health.structured_data import score_structured_data
from page_health.technical import score_technical_quality


@dataclass
class FixItem:
    """One entry in the ranked fix list (§8: "3-5 highest-impact fixes,
    ranked by point upside"). points_upside is 0 for hygiene notes that
    don't affect the score -- e.g. Structured Data's broken-JSON-LD-block
    count, which is reported even on a page that scored full parsing
    points (Day 3 decision #2)."""

    component: str
    description: str
    points_upside: int


@dataclass
class PageScoreResult:
    """Top-level result for a single scored page.

    score is None when the page is Unscored (§4 Tier 1) -- never a
    misleadingly low number. When present, it's the 0-100 PROJECTED value
    (Decision C above); score_breakdown carries the raw achieved/achievable
    points this was projected from, for anyone who wants the unprojected
    picture.
    """

    url: str
    final_url: Optional[str]

    confidence_tier: ConfidenceTier
    confidence_reason_codes: list
    unscored_reason: Optional[str]
    visible_text_word_count: Optional[int]

    score: Optional[int]  # 0-100, projected; None if Unscored
    achieved_points: Optional[int]
    achievable_max_points: Optional[int]  # varies per page; see module docstring

    crawlability: Optional[object] = None
    content_structure: Optional[object] = None
    structured_data: Optional[object] = None
    technical_quality: Optional[object] = None
    search_evidence: Optional[object] = None

    fixes: list = field(default_factory=list)


def _crawlability_fixes(result) -> list:
    fixes = []
    if result.status_points == 0:
        fixes.append(FixItem("Crawlability", f"Fix HTTP status (currently {result.status_code})", 10))
    if result.noindex_points == 0:
        fixes.append(FixItem("Crawlability", f"Remove noindex directive (source: {result.noindex_source})", 6))
    if result.canonical_points == 0:
        fixes.append(FixItem("Crawlability", f"Fix canonical tag ({result.canonical_reason})", 4))
    return fixes


def _content_structure_fixes(result) -> list:
    fixes = []
    if result.title_points == 0:
        fixes.append(FixItem("Content Structure", "Add a page title", 4))
    if result.h1_points == 0:
        fixes.append(FixItem("Content Structure", f"Use exactly one H1 (found {result.h1_count})", 4))
    if result.heading_hierarchy_points < 4:
        fixes.append(FixItem("Content Structure", "Improve heading hierarchy (add H2s, don't skip levels)", 4 - result.heading_hierarchy_points))
    if result.body_copy_points == 0:
        fixes.append(FixItem("Content Structure", f"Add more body copy (currently {result.visible_text_word_count} words)", 4))
    if result.lists_tables_faq_points == 0:
        fixes.append(FixItem("Content Structure", "Add a list, table, or FAQ block", 4))
    return fixes


def _structured_data_fixes(result) -> list:
    fixes = []
    if result.presence_points == 0:
        fixes.append(FixItem("Structured Data", "Add JSON-LD structured data", 3))
    elif result.parsing_points == 0:
        fixes.append(FixItem("Structured Data", "Fix invalid JSON-LD (all blocks failed to parse)", 4))
    elif result.type_specificity_points == 0:
        fixes.append(FixItem("Structured Data", "Use a specific schema.org @type", 4))
    elif result.required_properties_points == 0 and result.missing_required_properties:
        fixes.append(FixItem(
            "Structured Data",
            f"Add missing required properties: {', '.join(result.missing_required_properties)}",
            4,
        ))
    # Reported unconditionally regardless of score, per Day 3 decision #2 --
    # even a page scoring full parsing points can have a broken block hiding
    # among valid ones.
    if result.invalid_block_count > 0:
        fixes.append(FixItem(
            "Structured Data",
            f"Clean up {result.invalid_block_count} broken JSON-LD block(s)",
            0,
        ))
    return fixes


def _technical_quality_fixes(result) -> list:
    fixes = []
    if result.psi_available:
        if result.psi_seo_points == 0:
            fixes.append(FixItem("Technical Quality", f"Improve PageSpeed SEO score (currently {result.psi_seo_score})", 5))
        if result.psi_performance_points == 0:
            fixes.append(FixItem("Technical Quality", f"Improve PageSpeed performance score (currently {result.psi_performance_score})", 5))
    if result.alt_points == 0:
        fixes.append(FixItem("Technical Quality", "Add alt text to images", 5))
    if result.internal_link_points == 0:
        fixes.append(FixItem("Technical Quality", f"Add more internal links (currently {result.internal_link_count})", 5))
    return fixes


def _search_evidence_fixes(result) -> list:
    # Not this page's fault when GSC is unavailable -- no fix to suggest.
    if not result.gsc_available:
        return []
    fixes = []
    if result.impressions_points == 0:
        fixes.append(FixItem(
            "Search Evidence",
            f"Grow organic impressions (currently {result.impressions}) -- this also gates CTR and query diversity",
            15,
        ))
    else:
        if result.ctr_points == 0:
            fixes.append(FixItem("Search Evidence", f"Improve click-through rate (currently {result.ctr:.1%})", 5))
        if result.query_diversity_points == 0:
            fixes.append(FixItem("Search Evidence", f"Rank for more distinct queries (currently {result.distinct_query_count})", 5))
    return fixes


def _build_fix_list(crawlability, content, structured_data, technical, search_evidence) -> list:
    all_fixes = (
        _crawlability_fixes(crawlability)
        + _content_structure_fixes(content)
        + _structured_data_fixes(structured_data)
        + _technical_quality_fixes(technical)
        + _search_evidence_fixes(search_evidence)
    )
    return sorted(all_fixes, key=lambda fix: fix.points_upside, reverse=True)


def score_page(
    url: str,
    psi_api_key: Optional[str] = None,
    gsc_credentials_path: Optional[str] = None,
    gsc_site_url: Optional[str] = None,
) -> PageScoreResult:
    """Fetch and score a single page end-to-end.

    Per §12, this runs with zero WeekLift dependency until Search Evidence
    is invoked -- PSI and GSC credentials are both optional; their absence
    just means those components rescale out rather than the whole run
    failing.
    """
    fetch_result = fetch_page(url)
    confidence = assess_confidence(fetch_result)

    if confidence.tier == ConfidenceTier.UNSCORED:
        return PageScoreResult(
            url=url,
            final_url=fetch_result.final_url if fetch_result.error is None else None,
            confidence_tier=confidence.tier,
            confidence_reason_codes=confidence.reason_codes,
            unscored_reason=confidence.unscored_reason,
            visible_text_word_count=None,
            score=None,
            achieved_points=None,
            achievable_max_points=None,
        )

    crawlability = score_crawlability(fetch_result)
    content = score_content_structure(fetch_result, confidence)
    structured_data = score_structured_data(fetch_result)

    pagespeed_result = fetch_pagespeed_data(url, api_key=psi_api_key)
    technical = score_technical_quality(fetch_result, pagespeed_result)

    gsc_result = fetch_search_console_data(
        url, site_url=gsc_site_url, credentials_path=gsc_credentials_path
    )
    search_evidence = score_search_evidence(gsc_result)

    achieved_points = (
        crawlability.points
        + content.points
        + structured_data.points
        + technical.points
        + search_evidence.points
    )
    achievable_max_points = (
        crawlability.max_points
        + content.max_points
        + structured_data.max_points
        + technical.max_points
        + search_evidence.max_points
    )
    # Decision C: always projected onto 0-100 -- see module docstring.
    # achievable_max_points is never 0 here (Crawlability + Content
    # Structure alone contribute 40 unconditionally), so this division is
    # always safe.
    score = round(achieved_points / achievable_max_points * 100)

    fixes = _build_fix_list(crawlability, content, structured_data, technical, search_evidence)

    return PageScoreResult(
        url=url,
        final_url=fetch_result.final_url,
        confidence_tier=confidence.tier,
        confidence_reason_codes=confidence.reason_codes,
        unscored_reason=None,
        visible_text_word_count=confidence.visible_text_word_count,
        score=score,
        achieved_points=achieved_points,
        achievable_max_points=achievable_max_points,
        crawlability=crawlability,
        content_structure=content,
        structured_data=structured_data,
        technical_quality=technical,
        search_evidence=search_evidence,
        fixes=fixes,
    )
