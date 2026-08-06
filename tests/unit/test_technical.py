"""Tests for page_health.technical.

Covers SCOPE_OF_WORK.md §3's Technical Quality signals as proposed and
signed off on Day 4: binary PSI gates at 90, zero-images-passes for alt
coverage, the internal-linking threshold, and PSI-unavailable handling.
No network calls -- PageSpeedResult is always constructed directly and
injected, matching the pure-function contract.
"""

from page_health.fetch import FetchResult
from page_health.pagespeed_client import PageSpeedResult
from page_health.technical import score_technical_quality

PAGE_URL = "https://example.com/page"


def make_fetch_result(html: str, final_url: str = PAGE_URL) -> FetchResult:
    return FetchResult(
        original_url=final_url,
        final_url=final_url,
        status_code=200,
        headers={},
        html=html,
        error=None,
    )


def make_psi_result(seo=95, performance=95, error=None, status_code=200):
    return PageSpeedResult(seo_score=seo, performance_score=performance, error=error, status_code=status_code)


def page(body: str) -> str:
    return f"<html><head></head><body>{body}</body></html>"


def score(html: str, psi=None, final_url: str = PAGE_URL):
    fetch_result = make_fetch_result(html, final_url=final_url)
    psi = psi or make_psi_result()
    return score_technical_quality(fetch_result, psi)


# --- PSI SEO / performance gates (binary at 90) ---------------------------


def test_psi_seo_score_at_threshold_scores_full_points():
    result = score(page(""), psi=make_psi_result(seo=90))
    assert result.psi_seo_points == 5


def test_psi_seo_score_just_below_threshold_scores_zero():
    result = score(page(""), psi=make_psi_result(seo=89))
    assert result.psi_seo_points == 0


def test_psi_performance_score_at_threshold_scores_full_points():
    result = score(page(""), psi=make_psi_result(performance=90))
    assert result.psi_performance_points == 5


def test_psi_performance_score_below_threshold_scores_zero():
    result = score(page(""), psi=make_psi_result(performance=41))
    assert result.psi_performance_points == 0


def test_raw_psi_scores_always_included_in_output():
    result = score(page(""), psi=make_psi_result(seo=62, performance=30))
    assert result.psi_seo_score == 62
    assert result.psi_performance_score == 30


def test_psi_unavailable_rescales_the_psi_block_out_of_the_denominator():
    """Decision #15 (confirmed): PSI failure rescales the 10-point PSI
    block out of the denominator entirely, rather than scoring 0 for it --
    framed as PSI-scoped Unscored (§4), not a repeat of Structured Data's
    unknown-type pattern. Scoring 0 would assert a fact ("fails PSI's
    quality bar") that was never established."""
    psi = make_psi_result(
        seo=None,
        performance=None,
        error="PageSpeed Insights returned HTTP 403: bad key",
        status_code=403,
    )
    result = score(page(""), psi=psi)
    assert result.psi_available is False
    assert result.psi_error is not None
    assert result.psi_seo_points == 0
    assert result.psi_performance_points == 0
    assert result.psi_seo_score is None
    assert result.psi_performance_score is None
    assert result.max_points == 10  # PSI's 10-point block excluded, not counted against the page


def test_psi_unavailable_with_otherwise_perfect_page_scores_perfect_within_reduced_ceiling():
    """A page that aces everything PSI-independent should score full marks
    within its own (rescaled) ceiling, not be capped below 100% just
    because PSI specifically was unreachable."""
    body = '<img src="a.jpg" alt="d"><a href="/a">a</a><a href="/b">b</a><a href="/c">c</a>'
    psi = make_psi_result(seo=None, performance=None, error="PSI outage", status_code=500)
    result = score(page(body), psi=psi)
    assert result.max_points == 10
    assert result.points == 10  # alt (5) + internal linking (5), PSI excluded entirely


def test_psi_error_string_always_carried_even_though_it_does_not_affect_score():
    psi = make_psi_result(seo=None, performance=None, error="PageSpeed Insights returned HTTP 500: outage detail", status_code=500)
    result = score(page(""), psi=psi)
    assert "500" in result.psi_error
    assert "outage detail" in result.psi_error


# --- image alt coverage ---------------------------------------------------


def test_zero_images_passes_outright_decision_2():
    result = score(page("<p>no images here</p>"))
    assert result.total_images == 0
    assert result.alt_coverage is None
    assert result.alt_points == 5


def test_full_alt_coverage_scores_full_points():
    body = '<img src="a.jpg" alt="a description"><img src="b.jpg" alt="another">'
    result = score(page(body))
    assert result.total_images == 2
    assert result.images_with_alt == 2
    assert result.alt_coverage == 1.0
    assert result.alt_points == 5


def test_coverage_at_threshold_scores_full_points():
    # 9 of 10 images have alt = 90% = exactly at threshold
    imgs = "".join(f'<img src="{i}.jpg" alt="d{i}">' for i in range(9))
    imgs += '<img src="9.jpg">'
    result = score(page(imgs))
    assert result.alt_coverage == 0.9
    assert result.alt_points == 5


def test_coverage_below_threshold_scores_zero():
    imgs = '<img src="a.jpg" alt="d">' + "".join(f'<img src="{i}.jpg">' for i in range(9))
    result = score(page(imgs))
    assert result.alt_coverage == 0.1
    assert result.alt_points == 0


def test_empty_alt_attribute_counts_as_missing():
    body = '<img src="a.jpg" alt=""><img src="b.jpg" alt="">'
    result = score(page(body))
    assert result.images_with_alt == 0
    assert result.alt_coverage == 0.0
    assert result.alt_points == 0


def test_whitespace_only_alt_counts_as_missing():
    body = '<img src="a.jpg" alt="   ">'
    result = score(page(body))
    assert result.images_with_alt == 0


# --- internal linking -------------------------------------------------


def test_three_internal_links_scores_full_points():
    body = (
        '<a href="/page-a">a</a><a href="/page-b">b</a>'
        '<a href="https://example.com/page-c">c</a>'
    )
    result = score(page(body))
    assert result.internal_link_count == 3
    assert result.internal_link_points == 5


def test_two_internal_links_scores_zero():
    body = '<a href="/page-a">a</a><a href="/page-b">b</a>'
    result = score(page(body))
    assert result.internal_link_count == 2
    assert result.internal_link_points == 0


def test_external_links_not_counted_as_internal():
    body = (
        '<a href="/page-a">a</a><a href="/page-b">b</a><a href="/page-c">c</a>'
        '<a href="https://other-domain.com/x">external</a>'
    )
    result = score(page(body))
    assert result.internal_link_count == 3  # external one excluded


def test_anchor_mailto_and_tel_links_not_counted():
    body = (
        '<a href="/page-a">a</a><a href="/page-b">b</a><a href="/page-c">c</a>'
        '<a href="#section">anchor</a>'
        '<a href="mailto:hi@example.com">email</a>'
        '<a href="tel:+15555555555">call</a>'
    )
    result = score(page(body))
    assert result.internal_link_count == 3


def test_relative_links_resolve_against_final_url():
    body = '<a href="a">a</a><a href="./b">b</a><a href="../c">c</a>'
    result = score(page(body), final_url="https://example.com/blog/post")
    assert result.internal_link_count == 3


# --- overall totals -----------------------------------------------------


def test_fully_clean_page_scores_max_points():
    body = (
        '<img src="a.jpg" alt="d">'
        '<a href="/a">a</a><a href="/b">b</a><a href="/c">c</a>'
    )
    result = score(page(body), psi=make_psi_result(seo=95, performance=95))
    assert result.points == 20
    assert result.max_points == 20


def test_worst_case_page_scores_zero():
    body = '<img src="a.jpg"><a href="https://other.com/x">only external</a>'
    psi = make_psi_result(seo=None, performance=None, error="outage", status_code=500)
    result = score(page(body), psi=psi)
    assert result.points == 0
