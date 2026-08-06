"""Tests for page_health.structured_data.

Covers SCOPE_OF_WORK.md §3's Structured Data signals as proposed and signed
off on Day 3: staged/dependent gating, "any block parses" scoring with
unconditional broken-block diagnostics, permissive Article handling, and the
rescale-ceiling treatment for unknown types.
"""

import json

from page_health.fetch import FetchResult
from page_health.structured_data import (
    REQUIRED_PROPERTIES_STATUS_CHECKED,
    REQUIRED_PROPERTIES_STATUS_NOT_APPLICABLE,
    REQUIRED_PROPERTIES_STATUS_UNCHECKED_UNKNOWN_TYPE,
    score_structured_data,
)


def make_fetch_result(html: str) -> FetchResult:
    return FetchResult(
        original_url="https://example.com/page",
        final_url="https://example.com/page",
        status_code=200,
        headers={},
        html=html,
        error=None,
    )


def page_with_blocks(*json_ld_blocks: str) -> str:
    scripts = "".join(f'<script type="application/ld+json">{b}</script>' for b in json_ld_blocks)
    return f"<html><head>{scripts}</head><body>content</body></html>"


def score(*json_ld_blocks: str):
    return score_structured_data(make_fetch_result(page_with_blocks(*json_ld_blocks)))


ORG_BLOCK = json.dumps({"@context": "https://schema.org", "@type": "Organization", "name": "Acme", "url": "https://acme.example"})
ORG_BLOCK_MISSING_URL = json.dumps({"@context": "https://schema.org", "@type": "Organization", "name": "Acme"})
PRODUCT_BLOCK_COMPLETE = json.dumps({"@type": "Product", "name": "Widget", "offers": {"@type": "Offer", "price": "9.99"}})
PRODUCT_BLOCK_INCOMPLETE = json.dumps({"@type": "Product", "name": "Widget"})
WEBPAGE_BLOCK = json.dumps({"@type": "WebPage", "name": "Home"})
ARTICLE_BLOCK = json.dumps({"@type": "Article", "headline": "Big News", "datePublished": "2026-01-01"})
RECIPE_BLOCK = json.dumps({"@type": "Recipe", "name": "Soup"})  # not in REQUIRED_PROPERTIES map
BROKEN_JSON = '{"@type": "Organization", "name": "Acme"'  # missing closing brace


# --- presence -----------------------------------------------------------


def test_no_jsonld_scores_zero_everywhere():
    result = score()
    assert result.jsonld_present is False
    assert result.presence_points == 0
    assert result.parsing_points == 0
    assert result.type_specificity_points == 0
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_NOT_APPLICABLE
    assert result.points == 0
    assert result.max_points == 15


def test_jsonld_present_scores_presence_points():
    result = score(ORG_BLOCK)
    assert result.jsonld_present is True
    assert result.presence_points == 3


def test_whitespace_only_script_block_does_not_count_as_present():
    result = score("   \n  ")
    assert result.jsonld_present is False


# --- parsing (staged; gated by presence) ----------------------------------


def test_single_valid_block_scores_parsing_points():
    result = score(ORG_BLOCK)
    assert result.total_block_count == 1
    assert result.valid_block_count == 1
    assert result.invalid_block_count == 0
    assert result.parsing_points == 4


def test_single_broken_block_scores_zero_parsing():
    result = score(BROKEN_JSON)
    assert result.total_block_count == 1
    assert result.valid_block_count == 0
    assert result.invalid_block_count == 1
    assert len(result.block_parse_errors) == 1
    assert result.parsing_points == 0
    # staged: everything downstream is gated out too
    assert result.type_specificity_points == 0
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_NOT_APPLICABLE


def test_any_block_parses_scores_full_parsing_points_decision_2():
    """Decision #2: one good block among broken ones still scores full
    parsing points -- 'any parses', not 'all must parse'."""
    result = score(ORG_BLOCK, BROKEN_JSON)
    assert result.valid_block_count == 1
    assert result.invalid_block_count == 1
    assert result.parsing_points == 4


def test_broken_block_count_reported_even_at_full_parsing_score():
    """Decision #2: raw diagnostics report broken-block detail
    unconditionally, even when the page scores full points here -- so a
    future ranked-fix-list can surface it as its own fix item regardless of
    score."""
    result = score(ORG_BLOCK, BROKEN_JSON)
    assert result.parsing_points == 4  # full score
    assert result.invalid_block_count == 1  # but still flagged
    assert len(result.block_parse_errors) == 1


def test_multiple_valid_blocks_all_counted():
    result = score(ORG_BLOCK, PRODUCT_BLOCK_COMPLETE)
    assert result.total_block_count == 2
    assert result.valid_block_count == 2
    assert result.invalid_block_count == 0


# --- type specificity (staged; gated by parsing) --------------------------


def test_specific_type_scores_full_points():
    result = score(ORG_BLOCK)
    assert result.detected_type == "Organization"
    assert result.type_specificity_points == 4


def test_generic_type_scores_zero():
    result = score(WEBPAGE_BLOCK)
    assert result.detected_type is None
    assert result.type_specificity_points == 0
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_NOT_APPLICABLE


def test_article_counts_as_specific_decision_3():
    """Decision #3: Article is permissive, not denylisted."""
    result = score(ARTICLE_BLOCK)
    assert result.detected_type == "Article"
    assert result.type_specificity_points == 4


def test_type_as_array_with_one_specific_type_counts():
    block = json.dumps({"@type": ["Thing", "Product"], "name": "Widget", "offers": {"price": "1"}})
    result = score(block)
    assert result.detected_type == "Product"
    assert result.type_specificity_points == 4


def test_no_type_at_all_is_not_a_parseable_entity():
    # A JSON-LD block with no @type key at all -- valid JSON, but no entity
    # to extract types from, so type specificity is 0.
    block = json.dumps({"@context": "https://schema.org", "name": "Acme"})
    result = score(block)
    assert result.parsing_points == 4  # still valid JSON
    assert result.detected_type is None
    assert result.type_specificity_points == 0


def test_zero_type_gates_out_required_properties():
    result = score(WEBPAGE_BLOCK)
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_NOT_APPLICABLE
    assert result.required_properties_points == 0


# --- required properties (staged; gated by type specificity) --------------


def test_known_type_with_all_required_properties_scores_full_points():
    result = score(ORG_BLOCK)
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_CHECKED
    assert result.required_properties_points == 4
    assert result.missing_required_properties == []
    assert result.max_points == 15


def test_known_type_missing_required_property_scores_zero_and_lists_it():
    result = score(ORG_BLOCK_MISSING_URL)
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_CHECKED
    assert result.required_properties_points == 0
    assert "url" in result.missing_required_properties
    assert result.max_points == 15  # known type -- no rescale, this is a real fail


def test_product_any_of_group_satisfied_by_offers():
    result = score(PRODUCT_BLOCK_COMPLETE)
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_CHECKED
    assert result.required_properties_points == 4


def test_product_missing_both_any_of_options_fails():
    result = score(PRODUCT_BLOCK_INCOMPLETE)
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_CHECKED
    assert result.required_properties_points == 0
    assert any("offers" in item or "aggregateRating" in item for item in result.missing_required_properties)


def test_unknown_type_uses_rescale_ceiling_decision_4():
    """Decision #4: a specific-but-unrecognized type doesn't score 0 or 4
    for required properties -- it's excluded from the denominator entirely,
    and max_points drops from 15 to 11."""
    result = score(RECIPE_BLOCK)
    assert result.detected_type == "Recipe"
    assert result.type_specificity_points == 4
    assert result.required_properties_status == REQUIRED_PROPERTIES_STATUS_UNCHECKED_UNKNOWN_TYPE
    assert result.required_properties_points == 0
    assert result.max_points == 11
    # everything this page COULD earn, it did -- should be a perfect score
    # within its own (reduced) ceiling
    assert result.points == 11


def test_unknown_type_does_not_report_missing_properties():
    result = score(RECIPE_BLOCK)
    assert result.missing_required_properties == []


# --- overall totals ------------------------------------------------------


def test_fully_clean_known_type_page_scores_max_points():
    result = score(ORG_BLOCK)
    assert result.points == 15
    assert result.max_points == 15


def test_worst_case_page_scores_zero():
    result = score()
    assert result.points == 0
    assert result.max_points == 15


def test_raises_if_html_is_none():
    fetch_result = FetchResult(
        original_url="https://example.com/page",
        final_url="https://example.com/page",
        status_code=None,
        headers={},
        html=None,
        error="Connection refused",
    )
    import pytest

    with pytest.raises(ValueError):
        score_structured_data(fetch_result)
