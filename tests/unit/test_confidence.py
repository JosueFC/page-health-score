"""Tests for the three-tier confidence model in page_health.fetch (§4).

Covers all three tiers, both detectors firing independently and together,
and the permanent wording guards against the two reason strings drifting
back toward false certainty.
"""

from page_health.fetch import (
    MIN_VISIBLE_TEXT_WORDS,
    REASON_CLOSING_HTML_TAG_NOT_FOUND,
    REASON_LOW_VISIBLE_TEXT_WORD_COUNT,
    REASON_NO_HTTP_RESPONSE,
    REASON_NON_HTML_CONTENT_TYPE,
    ConfidenceTier,
    FetchResult,
    assess_confidence,
    count_visible_words,
)

LONG_PARAGRAPH = " ".join(["word"] * 200)
SHORT_PARAGRAPH = " ".join(["word"] * 10)


def make_fetch_result(html=None, error=None, headers=None, status_code=200):
    return FetchResult(
        original_url="https://example.com/page",
        final_url="https://example.com/page",
        status_code=status_code,
        headers=headers or {},
        html=html,
        error=error,
    )


def html_page(body: str, well_formed: bool = True) -> str:
    page = f"<html><head><title>t</title></head><body>{body}</body>"
    if well_formed:
        page += "</html>"
    return page


# --- Unscored tier -------------------------------------------------------


def test_connection_failure_is_unscored():
    fetch_result = make_fetch_result(html=None, error="Connection refused")
    result = assess_confidence(fetch_result)
    assert result.tier == ConfidenceTier.UNSCORED
    assert result.unscored_reason == REASON_NO_HTTP_RESPONSE
    assert result.visible_text_word_count is None


def test_no_html_without_error_is_unscored():
    fetch_result = make_fetch_result(html=None, error=None)
    result = assess_confidence(fetch_result)
    assert result.tier == ConfidenceTier.UNSCORED
    assert result.unscored_reason == REASON_NO_HTTP_RESPONSE


def test_non_html_content_type_is_unscored():
    fetch_result = make_fetch_result(
        html='{"not": "html"}',
        headers={"Content-Type": "application/json"},
    )
    result = assess_confidence(fetch_result)
    assert result.tier == ConfidenceTier.UNSCORED
    assert result.unscored_reason == REASON_NON_HTML_CONTENT_TYPE
    assert result.visible_text_word_count is None


def test_html_content_type_with_charset_is_not_unscored():
    fetch_result = make_fetch_result(
        html=html_page(LONG_PARAGRAPH),
        headers={"Content-Type": "text/html; charset=utf-8"},
    )
    result = assess_confidence(fetch_result)
    assert result.tier != ConfidenceTier.UNSCORED


def test_missing_content_type_header_is_not_unscored():
    fetch_result = make_fetch_result(html=html_page(LONG_PARAGRAPH), headers={})
    result = assess_confidence(fetch_result)
    assert result.tier != ConfidenceTier.UNSCORED


# --- high confidence -------------------------------------------------------


def test_complete_well_formed_page_with_enough_text_is_high_confidence():
    fetch_result = make_fetch_result(html=html_page(LONG_PARAGRAPH, well_formed=True))
    result = assess_confidence(fetch_result)
    assert result.tier == ConfidenceTier.SCORED_HIGH_CONFIDENCE
    assert result.reason_codes == []
    assert result.visible_text_word_count >= MIN_VISIBLE_TEXT_WORDS


# --- low confidence: closing tag detector -------------------------------


def test_missing_closing_html_tag_triggers_detector():
    fetch_result = make_fetch_result(html=html_page(LONG_PARAGRAPH, well_formed=False))
    result = assess_confidence(fetch_result)
    assert result.tier == ConfidenceTier.SCORED_LOW_CONFIDENCE
    assert REASON_CLOSING_HTML_TAG_NOT_FOUND in result.reason_codes


def test_closing_html_tag_detection_is_case_insensitive():
    html = f"<html><head></head><body>{LONG_PARAGRAPH}</body></HTML>"
    fetch_result = make_fetch_result(html=html)
    result = assess_confidence(fetch_result)
    assert REASON_CLOSING_HTML_TAG_NOT_FOUND not in result.reason_codes


# --- low confidence: word count detector --------------------------------


def test_low_word_count_triggers_detector():
    fetch_result = make_fetch_result(html=html_page(SHORT_PARAGRAPH, well_formed=True))
    result = assess_confidence(fetch_result)
    assert result.tier == ConfidenceTier.SCORED_LOW_CONFIDENCE
    assert REASON_LOW_VISIBLE_TEXT_WORD_COUNT in result.reason_codes
    assert result.visible_text_word_count < MIN_VISIBLE_TEXT_WORDS


def test_word_count_excludes_script_and_style_content():
    html = (
        "<html><head><title>t</title>"
        "<style>" + " ".join(["css"] * 200) + "</style>"
        "<script>" + " ".join(["js"] * 200) + "</script>"
        "</head><body>" + SHORT_PARAGRAPH + "</body></html>"
    )
    fetch_result = make_fetch_result(html=html)
    result = assess_confidence(fetch_result)
    assert REASON_LOW_VISIBLE_TEXT_WORD_COUNT in result.reason_codes


def test_count_visible_words_excludes_noscript():
    html = "<html><body>" + SHORT_PARAGRAPH + "<noscript>" + " ".join(["x"] * 500) + "</noscript></body></html>"
    assert count_visible_words(html) < MIN_VISIBLE_TEXT_WORDS


# --- both detectors together ---------------------------------------------


def test_both_detectors_can_fire_together():
    html = f"<html><head></head><body>{SHORT_PARAGRAPH}</body>"  # missing </html> AND thin
    fetch_result = make_fetch_result(html=html)
    result = assess_confidence(fetch_result)
    assert result.tier == ConfidenceTier.SCORED_LOW_CONFIDENCE
    assert REASON_CLOSING_HTML_TAG_NOT_FOUND in result.reason_codes
    assert REASON_LOW_VISIBLE_TEXT_WORD_COUNT in result.reason_codes
    assert len(result.reason_codes) == 2


# --- permanent wording guards (SCOPE_OF_WORK.md §4) -----------------------


def test_closing_html_tag_reason_string_never_implies_truncation():
    """Permanent guard: this reason code must never claim the page was
    truncated -- a legitimately sloppy-but-complete page looks identical to
    this check, and the code must not claim more than it observed."""
    assert "truncat" not in REASON_CLOSING_HTML_TAG_NOT_FOUND.lower()


def test_low_word_count_reason_string_never_implies_js_rendering():
    """Permanent guard: this reason code must never claim JS-rendering or
    an SPA was detected -- a legitimately short page (pricing, contact,
    landing page) looks identical to this check."""
    reason = REASON_LOW_VISIBLE_TEXT_WORD_COUNT.lower()
    assert "js" not in reason
    assert "spa" not in reason
    assert "rendered" not in reason
