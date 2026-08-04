"""Tests for page_health.content.

Covers SCOPE_OF_WORK.md §3's Content Structure signals as proposed and
signed off on Day 2: title, H1 (zero/one/multiple), heading hierarchy
(has-h2 and no-skipped-levels sub-signals), body copy sufficiency (shared
MIN_VISIBLE_TEXT_WORDS threshold with the confidence detector), and
lists/tables/FAQ block presence.
"""

from page_health.content import (
    LISTS_TABLES_FAQ_MATCH_FAQ,
    LISTS_TABLES_FAQ_MATCH_LIST,
    LISTS_TABLES_FAQ_MATCH_TABLE,
    LISTS_TABLES_FAQ_NO_MATCH,
    score_content_structure,
)
from page_health.fetch import FetchResult, assess_confidence

LONG_PARAGRAPH = " ".join(["word"] * 200)
SHORT_PARAGRAPH = " ".join(["word"] * 10)


def make_fetch_result(html: str) -> FetchResult:
    return FetchResult(
        original_url="https://example.com/page",
        final_url="https://example.com/page",
        status_code=200,
        headers={},
        html=html,
        error=None,
    )


def score(html: str):
    fetch_result = make_fetch_result(html)
    confidence = assess_confidence(fetch_result)
    return score_content_structure(fetch_result, confidence)


def page(head: str = "", body: str = "") -> str:
    return f"<html><head>{head}</head><body>{body}</body></html>"


# --- title ------------------------------------------------------------


def test_title_present_scores_full_points():
    result = score(page(head="<title>My Page</title>", body=LONG_PARAGRAPH))
    assert result.title_present is True
    assert result.title_points == 4


def test_title_absent_scores_zero():
    result = score(page(head="", body=LONG_PARAGRAPH))
    assert result.title_present is False
    assert result.title_points == 0


def test_title_whitespace_only_scores_zero():
    result = score(page(head="<title>   </title>", body=LONG_PARAGRAPH))
    assert result.title_present is False
    assert result.title_points == 0


# --- H1 ------------------------------------------------------------------


def test_exactly_one_h1_scores_full_points():
    result = score(page(body=f"<h1>Title</h1>{LONG_PARAGRAPH}"))
    assert result.h1_count == 1
    assert result.h1_points == 4


def test_zero_h1_scores_zero():
    result = score(page(body=LONG_PARAGRAPH))
    assert result.h1_count == 0
    assert result.h1_points == 0


def test_multiple_h1_scores_zero_not_partial_credit():
    result = score(page(body=f"<h1>One</h1><h1>Two</h1>{LONG_PARAGRAPH}"))
    assert result.h1_count == 2
    assert result.h1_points == 0


# --- heading hierarchy ------------------------------------------------


def test_h2_present_with_no_skipped_levels_scores_full_points():
    body = f"<h1>T</h1><h2>Section</h2>{LONG_PARAGRAPH}<h2>Another</h2><h3>Sub</h3>"
    result = score(page(body=body))
    assert result.has_h2 is True
    assert result.no_skipped_heading_levels is True
    assert result.heading_hierarchy_points == 4


def test_no_h2_at_all_scores_zero_for_hierarchy():
    result = score(page(body=f"<h1>T</h1>{LONG_PARAGRAPH}"))
    assert result.has_h2 is False
    assert result.heading_hierarchy_points == 0


def test_h3_before_any_h2_scores_zero_for_no_skipped_levels():
    body = f"<h1>T</h1><h3>Sub before h2</h3><h2>Section</h2>{LONG_PARAGRAPH}"
    result = score(page(body=body))
    assert result.has_h2 is True  # h2 exists...
    assert result.no_skipped_heading_levels is False  # ...but a h3 preceded it
    assert result.heading_hierarchy_points == 2  # only the has_h2 half


def test_h4_used_at_all_scores_zero_for_no_skipped_levels():
    body = f"<h1>T</h1><h2>Section</h2><h4>Too deep</h4>{LONG_PARAGRAPH}"
    result = score(page(body=body))
    assert result.has_h2 is True
    assert result.no_skipped_heading_levels is False
    assert result.heading_hierarchy_points == 2


def test_h3_after_h2_does_not_penalize():
    body = f"<h1>T</h1><h2>Section</h2><h3>Sub</h3>{LONG_PARAGRAPH}"
    result = score(page(body=body))
    assert result.no_skipped_heading_levels is True
    assert result.heading_hierarchy_points == 4


# --- body copy -------------------------------------------------------------


def test_body_copy_above_threshold_scores_full_points():
    result = score(page(body=LONG_PARAGRAPH))
    assert result.body_copy_points == 4
    assert result.visible_text_word_count >= 150


def test_body_copy_below_threshold_scores_zero():
    result = score(page(body=SHORT_PARAGRAPH))
    assert result.body_copy_points == 0
    assert result.visible_text_word_count < 150


def test_body_copy_word_count_is_always_present_in_output():
    """Raw word count included unconditionally, not just the boolean."""
    above = score(page(body=LONG_PARAGRAPH))
    below = score(page(body=SHORT_PARAGRAPH))
    assert above.visible_text_word_count is not None
    assert below.visible_text_word_count is not None


# --- lists / tables / faq -------------------------------------------------


def test_list_with_enough_items_scores_full_points():
    body = LONG_PARAGRAPH + "<ul><li>a</li><li>b</li><li>c</li></ul>"
    result = score(page(body=body))
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_MATCH_LIST
    assert result.lists_tables_faq_points == 4


def test_list_with_too_few_items_does_not_qualify():
    body = LONG_PARAGRAPH + "<ul><li>a</li><li>b</li></ul>"
    result = score(page(body=body))
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_NO_MATCH
    assert result.lists_tables_faq_points == 0


def test_table_with_enough_rows_scores_full_points():
    body = LONG_PARAGRAPH + "<table><tr><th>H</th></tr><tr><td>1</td></tr><tr><td>2</td></tr></table>"
    result = score(page(body=body))
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_MATCH_TABLE
    assert result.lists_tables_faq_points == 4


def test_table_with_too_few_rows_does_not_qualify():
    body = LONG_PARAGRAPH + "<table><tr><th>H</th></tr><tr><td>1</td></tr></table>"
    result = score(page(body=body))
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_NO_MATCH


def test_faq_block_with_two_qualifying_pairs_scores_full_points():
    body = (
        LONG_PARAGRAPH
        + "<h2>What is this?</h2><p>An answer.</p>"
        + "<h2>How does it work?</h2><p>Another answer.</p>"
    )
    result = score(page(body=body))
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_MATCH_FAQ
    assert result.lists_tables_faq_points == 4


def test_faq_block_with_only_one_qualifying_pair_does_not_qualify():
    body = LONG_PARAGRAPH + "<h2>What is this?</h2><p>An answer.</p>"
    result = score(page(body=body))
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_NO_MATCH


def test_question_heading_without_following_paragraph_does_not_count():
    body = (
        LONG_PARAGRAPH
        + "<h2>What is this?</h2><h2>How does it work?</h2><p>Only one answer.</p>"
    )
    result = score(page(body=body))
    # First question heading is immediately followed by another heading, not
    # a paragraph -- shouldn't count. Only one valid pair total.
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_NO_MATCH


def test_no_list_table_or_faq_scores_zero():
    result = score(page(body=LONG_PARAGRAPH))
    assert result.lists_tables_faq_match == LISTS_TABLES_FAQ_NO_MATCH
    assert result.lists_tables_faq_points == 0


# --- overall total -----------------------------------------------------


def test_fully_clean_page_scores_max_points():
    body = (
        "<h1>Title</h1><h2>Section</h2>"
        + LONG_PARAGRAPH
        + "<ul><li>a</li><li>b</li><li>c</li></ul>"
    )
    result = score(page(head="<title>My Page</title>", body=body))
    assert result.points == 20
    assert result.max_points == 20


def test_worst_case_page_scores_zero():
    result = score(page(head="", body=SHORT_PARAGRAPH))
    assert result.points == 0
