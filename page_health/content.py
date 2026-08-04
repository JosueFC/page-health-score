"""Content Structure scoring (SCOPE_OF_WORK.md §3). 20 points, five signals.

Pure function, no I/O -- takes an already-populated FetchResult and its
already-computed ConfidenceResult (from fetch.assess_confidence) and returns
a ContentStructureResult. Assumes fetch_result.html is not None, same
contract as crawlability.score_crawlability().

Point breakdown (proposed 4/4/4/4/4 split, signed off; tracked as a tunable
in SCOPE_OF_WORK.md §10 alongside the cross-component weights, not treated
as permanently settled):
    - Title present:                                    4 points
    - Exactly one H1:                                   4 points
    - Sensible heading hierarchy (2 + 2 sub-split):      4 points
    - Body copy sufficiency (>= MIN_VISIBLE_TEXT_WORDS): 4 points
    - Lists / tables / FAQ block present:                4 points

Body copy sufficiency deliberately reuses fetch.MIN_VISIBLE_TEXT_WORDS -- the
same constant that drives the low_visible_text_word_count confidence
detector (§4). This means a page scoring 0/4 here is always simultaneously
flagged low-confidence. That's accepted as intentional redundancy (option A
from the Day 2 proposal) rather than confusing: a thin page failing both
checks is the same fact seen from two angles, not two different facts.
"""

from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup

from page_health.fetch import MIN_VISIBLE_TEXT_WORDS, ConfidenceResult, FetchResult

TITLE_POINTS = 4
H1_POINTS = 4
HEADING_HIERARCHY_POINTS = 4
BODY_COPY_POINTS = 4
LISTS_TABLES_FAQ_POINTS = 4
MAX_POINTS = TITLE_POINTS + H1_POINTS + HEADING_HIERARCHY_POINTS + BODY_COPY_POINTS + LISTS_TABLES_FAQ_POINTS  # 20

# Minimum <li> children for a list to count toward the lists/tables/FAQ signal.
MIN_LIST_ITEMS = 3
# Minimum total <tr> rows (including header) for a table to count.
MIN_TABLE_ROWS = 3

LISTS_TABLES_FAQ_MATCH_LIST = "list"
LISTS_TABLES_FAQ_MATCH_TABLE = "table"
LISTS_TABLES_FAQ_MATCH_FAQ = "faq"
LISTS_TABLES_FAQ_NO_MATCH = "none"

_FAQ_HEADING_NAMES = ("h2", "h3", "h4")
_HEADING_AND_PARAGRAPH_NAMES = ("h2", "h3", "h4", "p")


@dataclass
class ContentStructureResult:
    """Score + raw diagnostics for the Content Structure component.

    Raw observed values are included unconditionally (not just booleans),
    matching the pattern established in crawlability.CrawlabilityResult and
    SCOPE_OF_WORK.md §8's output format requirement.
    """

    points: int
    max_points: int

    title_present: bool
    title_points: int

    h1_count: int
    h1_points: int

    has_h2: bool
    no_skipped_heading_levels: bool
    heading_hierarchy_points: int

    visible_text_word_count: Optional[int]  # sourced from the ConfidenceResult, not recomputed
    body_copy_points: int

    lists_tables_faq_match: str  # one of LISTS_TABLES_FAQ_MATCH_* or LISTS_TABLES_FAQ_NO_MATCH
    lists_tables_faq_points: int


def _score_title(soup: BeautifulSoup) -> tuple[bool, int]:
    title_tag = soup.find("title")
    present = title_tag is not None and bool(title_tag.get_text(strip=True))
    return present, (TITLE_POINTS if present else 0)


def _score_h1(soup: BeautifulSoup) -> tuple[int, int]:
    count = len(soup.find_all("h1"))
    # Binary, no partial credit: zero H1s and multiple H1s both score 0,
    # mirroring the Crawlability precedent that a confusing/ambiguous signal
    # is treated at least as harshly as no signal.
    points = H1_POINTS if count == 1 else 0
    return count, points


def _score_heading_hierarchy(soup: BeautifulSoup) -> tuple[bool, bool, int]:
    """2 points for having any <h2> at all, 2 points for not skipping levels.

    "Not skipping levels" (v1 scope): no <h3> appears before any <h2> has
    appeared in document order, and no <h4> or deeper is used at all --
    validating structure no deeper than H2/H3 per §3's stated scope.

    no_skipped_levels requires has_h2 as a precondition: with zero <h2> tags
    there's no hierarchy to have skipped a level within, so a page with no
    sub-headings at all must score 0 for this half too, not a vacuous pass.
    """
    has_h2 = bool(soup.find_all("h2"))

    has_deep_heading = bool(soup.find_all(["h4", "h5", "h6"]))
    seen_h2 = False
    h3_before_h2 = False
    for tag in soup.find_all(["h2", "h3"]):
        if tag.name == "h3" and not seen_h2:
            h3_before_h2 = True
            break
        if tag.name == "h2":
            seen_h2 = True
    no_skipped_levels = has_h2 and not h3_before_h2 and not has_deep_heading

    points = (2 if has_h2 else 0) + (2 if no_skipped_levels else 0)
    return has_h2, no_skipped_levels, points


def _score_body_copy(confidence: ConfidenceResult) -> int:
    word_count = confidence.visible_text_word_count or 0
    return BODY_COPY_POINTS if word_count >= MIN_VISIBLE_TEXT_WORDS else 0


def _has_qualifying_list(soup: BeautifulSoup) -> bool:
    for list_tag in soup.find_all(["ul", "ol"]):
        # Direct children only -- a nested list shouldn't inflate the parent's count.
        if len(list_tag.find_all("li", recursive=False)) >= MIN_LIST_ITEMS:
            return True
    return False


def _has_qualifying_table(soup: BeautifulSoup) -> bool:
    for table_tag in soup.find_all("table"):
        if len(table_tag.find_all("tr")) >= MIN_TABLE_ROWS:
            return True
    return False


def _has_qualifying_faq_block(soup: BeautifulSoup) -> bool:
    """>= 2 headings (h2-h4) ending in '?', each followed by at least one
    non-empty <p> before the next heading.

    Deliberately HTML-structural only -- does not look at FAQPage JSON-LD.
    That's Structured Data's (Day 3) territory; keeping this check scoped to
    HTML avoids the two components overlapping on schema validation.
    """
    faq_pairs_found = 0
    awaiting_paragraph = False
    for tag in soup.find_all(_HEADING_AND_PARAGRAPH_NAMES):
        if tag.name in _FAQ_HEADING_NAMES:
            awaiting_paragraph = tag.get_text(strip=True).endswith("?")
        elif tag.name == "p":
            if awaiting_paragraph and tag.get_text(strip=True):
                faq_pairs_found += 1
                awaiting_paragraph = False
    return faq_pairs_found >= 2


def _score_lists_tables_faq(soup: BeautifulSoup) -> tuple[str, int]:
    if _has_qualifying_list(soup):
        return LISTS_TABLES_FAQ_MATCH_LIST, LISTS_TABLES_FAQ_POINTS
    if _has_qualifying_table(soup):
        return LISTS_TABLES_FAQ_MATCH_TABLE, LISTS_TABLES_FAQ_POINTS
    if _has_qualifying_faq_block(soup):
        return LISTS_TABLES_FAQ_MATCH_FAQ, LISTS_TABLES_FAQ_POINTS
    return LISTS_TABLES_FAQ_NO_MATCH, 0


def score_content_structure(
    fetch_result: FetchResult, confidence: ConfidenceResult
) -> ContentStructureResult:
    """Score the Content Structure component (20 points).

    Assumes fetch_result.html is not None -- same upstream-gating contract
    as score_crawlability(). Takes confidence as a parameter rather than
    recomputing word count, so the two never drift out of sync.
    """
    if fetch_result.html is None:
        raise ValueError(
            "score_content_structure() requires fetch_result.html to be "
            "present. Unreachable pages must be gated upstream, not scored "
            "here -- see SCOPE_OF_WORK.md §4."
        )

    soup = BeautifulSoup(fetch_result.html, "html.parser")

    title_present, title_points = _score_title(soup)
    h1_count, h1_points = _score_h1(soup)
    has_h2, no_skipped_levels, heading_points = _score_heading_hierarchy(soup)
    body_copy_points = _score_body_copy(confidence)
    lists_tables_faq_match, lists_tables_faq_points = _score_lists_tables_faq(soup)

    total_points = (
        title_points
        + h1_points
        + heading_points
        + body_copy_points
        + lists_tables_faq_points
    )

    return ContentStructureResult(
        points=total_points,
        max_points=MAX_POINTS,
        title_present=title_present,
        title_points=title_points,
        h1_count=h1_count,
        h1_points=h1_points,
        has_h2=has_h2,
        no_skipped_heading_levels=no_skipped_levels,
        heading_hierarchy_points=heading_points,
        visible_text_word_count=confidence.visible_text_word_count,
        body_copy_points=body_copy_points,
        lists_tables_faq_match=lists_tables_faq_match,
        lists_tables_faq_points=lists_tables_faq_points,
    )
