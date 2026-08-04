"""Tests for page_health.crawlability.

Covers SCOPE_OF_WORK.md §3's confirmed signals:
    - 200 status / non-200 status
    - noindex via meta robots, via X-Robots-Tag (any user-agent scope), via
      both, and absent
    - canonical: self-referencing, absent, malformed (empty href,
      unresolvable relative, multiple conflicting), and resolvable-but-
      points-elsewhere -- with distinguishable reasons at the same 0/4 score.
"""

import pytest

from page_health.crawlability import (
    CANONICAL_ABSENT,
    CANONICAL_MALFORMED_EMPTY_HREF,
    CANONICAL_MALFORMED_MULTIPLE,
    CANONICAL_MALFORMED_UNRESOLVABLE,
    CANONICAL_POINTS_ELSEWHERE,
    CANONICAL_SELF_REFERENCING,
    score_crawlability,
)
from page_health.fetch import FetchResult

PAGE_URL = "https://example.com/page"


def make_fetch_result(
    html: str,
    status_code: int = 200,
    headers: dict | None = None,
    final_url: str = PAGE_URL,
) -> FetchResult:
    return FetchResult(
        original_url=final_url,
        final_url=final_url,
        status_code=status_code,
        headers=headers or {},
        html=html,
        error=None,
    )


def bare_html(head_extra: str = "") -> str:
    return f"<html><head>{head_extra}</head><body>hello</body></html>"


# --- status signal -----------------------------------------------------


def test_200_status_scores_full_status_points():
    fetch_result = make_fetch_result(bare_html(), status_code=200)
    result = score_crawlability(fetch_result)
    assert result.status_points == 10
    assert result.status_code == 200


@pytest.mark.parametrize("status_code", [301, 404, 500, 503])
def test_non_200_status_scores_zero_status_points(status_code):
    fetch_result = make_fetch_result(bare_html(), status_code=status_code)
    result = score_crawlability(fetch_result)
    assert result.status_points == 0
    assert result.status_code == status_code


# --- noindex signal ------------------------------------------------------


def test_no_noindex_signals_scores_full_noindex_points():
    fetch_result = make_fetch_result(bare_html())
    result = score_crawlability(fetch_result)
    assert result.noindex is False
    assert result.noindex_source is None
    assert result.noindex_points == 6


def test_noindex_via_meta_robots_scores_zero():
    html = bare_html('<meta name="robots" content="noindex">')
    fetch_result = make_fetch_result(html)
    result = score_crawlability(fetch_result)
    assert result.noindex is True
    assert result.noindex_source == "meta_robots"
    assert result.noindex_points == 0


def test_noindex_via_x_robots_tag_header_scores_zero():
    fetch_result = make_fetch_result(bare_html(), headers={"X-Robots-Tag": "noindex"})
    result = score_crawlability(fetch_result)
    assert result.noindex is True
    assert result.noindex_source == "x_robots_tag"
    assert result.noindex_points == 0


def test_noindex_via_user_agent_scoped_x_robots_tag_still_fails():
    """v1 default (SCOPE_OF_WORK.md §10): any noindex token fails, regardless
    of user-agent scoping."""
    fetch_result = make_fetch_result(bare_html(), headers={"X-Robots-Tag": "googlebot: noindex"})
    result = score_crawlability(fetch_result)
    assert result.noindex is True
    assert result.noindex_source == "x_robots_tag"
    assert result.noindex_points == 0


def test_noindex_via_both_meta_and_header_reports_both_sources():
    html = bare_html('<meta name="robots" content="noindex">')
    fetch_result = make_fetch_result(html, headers={"X-Robots-Tag": "noindex"})
    result = score_crawlability(fetch_result)
    assert result.noindex is True
    assert result.noindex_source == "meta_robots+x_robots_tag"
    assert result.noindex_points == 0


def test_other_x_robots_tag_directives_without_noindex_do_not_fail():
    fetch_result = make_fetch_result(bare_html(), headers={"X-Robots-Tag": "nofollow"})
    result = score_crawlability(fetch_result)
    assert result.noindex is False
    assert result.noindex_points == 6


# --- canonical signal ------------------------------------------------------


def test_canonical_self_referencing_scores_full_points():
    html = bare_html(f'<link rel="canonical" href="{PAGE_URL}">')
    fetch_result = make_fetch_result(html)
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_SELF_REFERENCING
    assert result.canonical_points == 4


def test_canonical_self_referencing_relative_href_resolves_correctly():
    html = bare_html('<link rel="canonical" href="/page">')
    fetch_result = make_fetch_result(html, final_url=PAGE_URL)
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_SELF_REFERENCING
    assert result.canonical_points == 4


def test_canonical_self_referencing_ignores_trailing_slash_difference():
    html = bare_html(f'<link rel="canonical" href="{PAGE_URL}/">')
    fetch_result = make_fetch_result(html, final_url=PAGE_URL)
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_SELF_REFERENCING
    assert result.canonical_points == 4


def test_canonical_absent_scores_zero():
    fetch_result = make_fetch_result(bare_html())
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_ABSENT
    assert result.canonical_value is None
    assert result.canonical_points == 0


def test_canonical_malformed_empty_href_scores_zero():
    html = bare_html('<link rel="canonical" href="">')
    fetch_result = make_fetch_result(html)
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_MALFORMED_EMPTY_HREF
    assert result.canonical_points == 0


def test_canonical_malformed_unresolvable_relative_scores_zero():
    # href="http://" is absolute-looking (has a scheme) but has no host, so
    # it can never resolve to a usable URL regardless of base -- unlike a
    # genuinely relative path, which urljoin always resolves against the
    # base's scheme+host (see the self-referencing-relative-href test above).
    html = bare_html('<link rel="canonical" href="http://">')
    fetch_result = make_fetch_result(html)
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_MALFORMED_UNRESOLVABLE
    assert result.canonical_points == 0


def test_canonical_multiple_conflicting_scores_zero():
    html = bare_html(
        '<link rel="canonical" href="https://example.com/page-a">'
        '<link rel="canonical" href="https://example.com/page-b">'
    )
    fetch_result = make_fetch_result(html)
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_MALFORMED_MULTIPLE
    assert result.canonical_points == 0


def test_canonical_resolvable_but_points_elsewhere_scores_zero():
    html = bare_html('<link rel="canonical" href="https://example.com/other-page">')
    fetch_result = make_fetch_result(html, final_url=PAGE_URL)
    result = score_crawlability(fetch_result)
    assert result.canonical_reason == CANONICAL_POINTS_ELSEWHERE
    assert result.canonical_points == 0


def test_malformed_and_points_elsewhere_are_distinguishable_at_same_score():
    """Both score 0/4 but must be tellable apart in the raw output --
    they're different fixes for a reader even at an identical point value."""
    absent = score_crawlability(make_fetch_result(bare_html()))
    elsewhere_html = bare_html('<link rel="canonical" href="https://example.com/other">')
    elsewhere = score_crawlability(make_fetch_result(elsewhere_html, final_url=PAGE_URL))

    assert absent.canonical_points == elsewhere.canonical_points == 0
    assert absent.canonical_reason != elsewhere.canonical_reason


# --- overall total -----------------------------------------------------


def test_fully_clean_page_scores_max_points():
    html = bare_html(f'<link rel="canonical" href="{PAGE_URL}">')
    fetch_result = make_fetch_result(html, status_code=200)
    result = score_crawlability(fetch_result)
    assert result.points == 20
    assert result.max_points == 20


def test_worst_case_page_scores_zero():
    html = bare_html(
        '<meta name="robots" content="noindex">'
        '<link rel="canonical" href="">'
    )
    fetch_result = make_fetch_result(html, status_code=404)
    result = score_crawlability(fetch_result)
    assert result.points == 0


# --- upstream contract ------------------------------------------------------


def test_raises_if_html_is_none():
    """Unreachable pages must be gated before reaching this function --
    it's not this function's job to handle that case (§4)."""
    fetch_result = FetchResult(
        original_url=PAGE_URL,
        final_url=PAGE_URL,
        status_code=None,
        headers={},
        html=None,
        error="Connection refused",
    )
    with pytest.raises(ValueError):
        score_crawlability(fetch_result)
