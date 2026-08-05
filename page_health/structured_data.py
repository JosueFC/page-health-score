"""Structured Data scoring (SCOPE_OF_WORK.md §3). 15 points, four staged signals.

Pure function, no I/O -- takes an already-populated FetchResult and returns a
StructuredDataResult. Assumes fetch_result.html is not None, same contract as
crawlability.score_crawlability() / content.score_content_structure().

Signals are STAGED, not independent (Day 3 sign-off, decision #1): each
signal gates the next. A page with no JSON-LD scores 0/15 outright rather
than picking up partial credit for signals it never had a chance to fail.

    - JSON-LD present:                 3 points
    - Valid parsing (any block):       4 points  (decision #2, see below)
    - Type specificity:                4 points  (decision #3: `Article` is
                                                    treated as specific, not
                                                    denylisted)
    - Required properties filled:      4 points  (decision #4: unknown types
                                                    rescale the page's ceiling
                                                    to 11, rather than scoring
                                                    0 or 4 for something this
                                                    tool can't actually verify)

Decision #2 detail: "valid parsing" scores full points if AT LEAST ONE
JSON-LD block parses -- not "all blocks must parse". This is more forgiving
than the harsh-treatment precedent used elsewhere (Crawlability's canonical,
Content Structure's H1), because real sites accumulate schema from multiple
sources over time and one stale broken block souring an otherwise-healthy
page felt like the wrong trade. To not lose the signal entirely, broken-block
count and per-block error detail are captured in the raw diagnostic output
UNCONDITIONALLY -- even on a page that scores full points here -- so a future
ranked-fix-list (§8, score.py) can surface "you have N broken JSON-LD
blocks" as its own separately-ranked fix item regardless of the score.
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from bs4 import BeautifulSoup

from page_health.fetch import FetchResult

PRESENCE_POINTS = 3
PARSING_POINTS = 4
TYPE_SPECIFICITY_POINTS = 4
REQUIRED_PROPERTIES_POINTS = 4
MAX_POINTS = PRESENCE_POINTS + PARSING_POINTS + TYPE_SPECIFICITY_POINTS + REQUIRED_PROPERTIES_POINTS  # 15

# Types considered too generic to count for type-specificity, regardless of
# how confidently they're used. Comparison is case-insensitive.
# `Article` is deliberately NOT here (Day 3 decision #3, permissive) --
# schema.org doesn't deprecate or discourage plain Article, and false
# negatives here (dinging a legitimately-typed page) are worse for a hygiene
# check than false positives.
GENERIC_TYPE_DENYLIST = {"thing", "webpage", "website", "creativework"}

# Required-property rules for a small, hardcoded, growing set of common
# types. "required": keys that must be present and non-empty (truthy).
# "any_of": optional group where at least one key must be present and
# non-empty, in addition to "required".
#
# A type NOT in this map is not a failure -- it's unverifiable, and is
# handled via the rescale-ceiling path in score_structured_data() rather
# than being scored 0 or 4 for something never actually checked. This map
# is explicitly a living/growing list -- tracked in SCOPE_OF_WORK.md §10.
REQUIRED_PROPERTIES = {
    "Organization": {"required": ["name", "url"]},
    "LocalBusiness": {"required": ["name", "url"]},
    "Product": {"required": ["name"], "any_of": ["offers", "aggregateRating"]},
    "Article": {"required": ["headline", "datePublished"]},
    "BlogPosting": {"required": ["headline", "datePublished"]},
    "NewsArticle": {"required": ["headline", "datePublished"]},
    "BreadcrumbList": {"required": ["itemListElement"]},
    "FAQPage": {"required": ["mainEntity"]},
}
_REQUIRED_PROPERTIES_LOOKUP = {key.lower(): key for key in REQUIRED_PROPERTIES}

REQUIRED_PROPERTIES_STATUS_NOT_APPLICABLE = "not_applicable"  # gated out by an earlier failed signal
REQUIRED_PROPERTIES_STATUS_UNCHECKED_UNKNOWN_TYPE = "unchecked_unknown_type"  # specific type, but not in our map
REQUIRED_PROPERTIES_STATUS_CHECKED = "checked"  # type known, check actually ran


@dataclass
class StructuredDataResult:
    """Score + raw diagnostics for the Structured Data component.

    max_points is normally 15, but is reduced to 11 when the detected type
    is specific but not in REQUIRED_PROPERTIES -- see
    required_properties_status. This is the one component where max_points
    is NOT a constant across all pages; callers combining sub-scores (later,
    score.py) must read max_points per-result rather than assuming 15.
    """

    points: int
    max_points: int

    jsonld_present: bool
    presence_points: int

    total_block_count: int
    valid_block_count: int
    invalid_block_count: int  # always populated, regardless of parsing_points -- see module docstring
    block_parse_errors: list  # str messages, one per invalid block, always populated
    parsing_points: int

    detected_type: Optional[str]  # the specific @type used for the rest of the pipeline, if any
    type_specificity_points: int

    required_properties_status: str  # one of REQUIRED_PROPERTIES_STATUS_*
    required_properties_points: int
    missing_required_properties: list = field(default_factory=list)  # populated only when status == CHECKED and incomplete


def _extract_jsonld_blocks(soup: BeautifulSoup) -> list:
    """Raw text content of every <script type="application/ld+json"> block
    with non-whitespace content."""
    blocks = []
    for tag in soup.find_all("script", attrs={"type": True}):
        type_attr = (tag.get("type") or "").strip().lower()
        if type_attr != "application/ld+json":
            continue
        text = tag.string if tag.string is not None else tag.get_text()
        if text and text.strip():
            blocks.append(text)
    return blocks


def _parse_blocks(raw_blocks: list) -> tuple:
    """Returns (parsed_values, error_messages) -- parsed_values holds only
    the successfully-parsed JSON values; error_messages holds one string per
    block that failed to parse."""
    parsed_values = []
    error_messages = []
    for raw in raw_blocks:
        try:
            parsed_values.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            error_messages.append(str(exc))
    return parsed_values, error_messages


def _collect_entities(parsed_values: list) -> list:
    """Flatten parsed JSON-LD values into a list of entity dicts that each
    have an @type. Handles: a single object, a top-level array of objects,
    and @graph nesting (one level of recursion is enough for v1's common
    cases)."""
    entities = []

    def collect(value):
        if isinstance(value, dict):
            if "@type" in value:
                entities.append(value)
            graph = value.get("@graph")
            if isinstance(graph, list):
                for item in graph:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for parsed in parsed_values:
        collect(parsed)
    return entities


def _entity_types(entity: dict) -> list:
    raw_type = entity.get("@type")
    if isinstance(raw_type, str):
        return [raw_type.strip()] if raw_type.strip() else []
    if isinstance(raw_type, list):
        return [t.strip() for t in raw_type if isinstance(t, str) and t.strip()]
    return []


def _find_specific_entity(entities: list) -> tuple:
    """Returns (entity, specific_type) for the first entity (in document
    order) that has at least one non-generic type, or (None, None)."""
    for entity in entities:
        for type_name in _entity_types(entity):
            if type_name.lower() not in GENERIC_TYPE_DENYLIST:
                return entity, type_name
    return None, None


def _check_required_properties(entity: dict, type_name: str) -> list:
    """Returns a list of missing property names (empty list = all present)."""
    canonical_key = _REQUIRED_PROPERTIES_LOOKUP.get(type_name.lower())
    rules = REQUIRED_PROPERTIES[canonical_key]
    missing = [key for key in rules["required"] if not entity.get(key)]

    any_of = rules.get("any_of")
    if any_of and not any(entity.get(key) for key in any_of):
        missing.append(f"one of {any_of}")

    return missing


def score_structured_data(fetch_result: FetchResult) -> StructuredDataResult:
    """Score the Structured Data component for an already-fetched page.

    Assumes fetch_result.html is not None -- same upstream-gating contract
    as the other pure scoring functions.
    """
    if fetch_result.html is None:
        raise ValueError(
            "score_structured_data() requires fetch_result.html to be "
            "present. Unreachable pages must be gated upstream, not scored "
            "here -- see SCOPE_OF_WORK.md §4."
        )

    soup = BeautifulSoup(fetch_result.html, "html.parser")

    # --- signal 1: presence --------------------------------------------
    raw_blocks = _extract_jsonld_blocks(soup)
    jsonld_present = len(raw_blocks) > 0
    presence_points = PRESENCE_POINTS if jsonld_present else 0

    # --- signal 2: parsing (any block parses -- decision #2) -----------
    parsed_values, block_parse_errors = _parse_blocks(raw_blocks)
    total_block_count = len(raw_blocks)
    valid_block_count = len(parsed_values)
    invalid_block_count = len(block_parse_errors)
    parsing_points = PARSING_POINTS if (jsonld_present and valid_block_count >= 1) else 0

    # --- signal 3: type specificity (Article counts -- decision #3) ----
    entities = _collect_entities(parsed_values)
    detected_entity, detected_type = (None, None)
    if parsing_points > 0:
        detected_entity, detected_type = _find_specific_entity(entities)
    type_specificity_points = TYPE_SPECIFICITY_POINTS if detected_type else 0

    # --- signal 4: required properties (rescale ceiling -- decision #4) --
    max_points = MAX_POINTS
    missing_required_properties: list = []
    if type_specificity_points == 0:
        required_properties_status = REQUIRED_PROPERTIES_STATUS_NOT_APPLICABLE
        required_properties_points = 0
    elif detected_type.lower() not in _REQUIRED_PROPERTIES_LOOKUP:
        required_properties_status = REQUIRED_PROPERTIES_STATUS_UNCHECKED_UNKNOWN_TYPE
        required_properties_points = 0
        max_points = MAX_POINTS - REQUIRED_PROPERTIES_POINTS  # rescale: this page's ceiling is 11, not 15
    else:
        required_properties_status = REQUIRED_PROPERTIES_STATUS_CHECKED
        missing_required_properties = _check_required_properties(detected_entity, detected_type)
        required_properties_points = REQUIRED_PROPERTIES_POINTS if not missing_required_properties else 0

    total_points = presence_points + parsing_points + type_specificity_points + required_properties_points

    return StructuredDataResult(
        points=total_points,
        max_points=max_points,
        jsonld_present=jsonld_present,
        presence_points=presence_points,
        total_block_count=total_block_count,
        valid_block_count=valid_block_count,
        invalid_block_count=invalid_block_count,
        block_parse_errors=block_parse_errors,
        parsing_points=parsing_points,
        detected_type=detected_type,
        type_specificity_points=type_specificity_points,
        required_properties_status=required_properties_status,
        required_properties_points=required_properties_points,
        missing_required_properties=missing_required_properties,
    )
