"""Tests for page_health.cli.

score_page is mocked throughout -- these test the CLI's own behavior
(argument handling, URL normalization, output formatting), not the scoring
pipeline itself (covered by test_score.py). This file didn't exist before
the Day 6 audit -- its absence is exactly why the missing __main__.py bug
and the invalid-URL reason-code bug weren't caught by the test suite.
"""

from unittest.mock import patch

import pytest

from page_health.cli import _normalize_url, main
from page_health.fetch import ConfidenceTier
from page_health.score import PageScoreResult


def make_result(
    url="https://example.com",
    final_url="https://example.com",
    confidence_tier=ConfidenceTier.SCORED_HIGH_CONFIDENCE,
    unscored_reason=None,
    score=87,
    fixes=None,
):
    return PageScoreResult(
        url=url,
        final_url=final_url,
        confidence_tier=confidence_tier,
        confidence_reason_codes=[],
        unscored_reason=unscored_reason,
        visible_text_word_count=300,
        score=score,
        achieved_points=78,
        achievable_max_points=90,
        fixes=fixes or [],
    )


# --- URL normalization -------------------------------------------------


def test_bare_domain_gets_https_prefix():
    url, changed = _normalize_url("weeklift.com")
    assert url == "https://weeklift.com"
    assert changed is True


def test_url_with_scheme_is_unchanged():
    url, changed = _normalize_url("https://weeklift.com")
    assert url == "https://weeklift.com"
    assert changed is False


def test_http_scheme_is_left_as_is_not_forced_to_https():
    url, changed = _normalize_url("http://weeklift.com")
    assert url == "http://weeklift.com"
    assert changed is False


@patch("page_health.cli.score_page")
def test_normalization_notice_is_printed_to_stderr(mock_score, capsys):
    mock_score.return_value = make_result()
    main(["weeklift.com"])
    captured = capsys.readouterr()
    assert "No scheme provided" in captured.err
    assert "https://weeklift.com" in captured.err


@patch("page_health.cli.score_page")
def test_normalized_url_is_what_gets_scored(mock_score):
    mock_score.return_value = make_result()
    main(["weeklift.com"])
    called_url = mock_score.call_args[0][0]
    assert called_url == "https://weeklift.com"


@patch("page_health.cli.score_page")
def test_no_normalization_notice_when_scheme_already_present(mock_score, capsys):
    mock_score.return_value = make_result()
    main(["https://weeklift.com"])
    captured = capsys.readouterr()
    assert "No scheme provided" not in captured.err


# --- usage --------------------------------------------------------------


def test_no_args_prints_usage_and_returns_error_code(capsys):
    exit_code = main([])
    assert exit_code == 2
    captured = capsys.readouterr()
    assert "Usage" in captured.err


def test_too_many_args_prints_usage_and_returns_error_code(capsys):
    exit_code = main(["https://a.com", "https://b.com"])
    assert exit_code == 2


# --- output formatting ---------------------------------------------------


@patch("page_health.cli.score_page")
def test_unscored_page_prints_reason_and_returns_zero(mock_score, capsys):
    mock_score.return_value = make_result(
        confidence_tier=ConfidenceTier.UNSCORED, unscored_reason="invalid_url", score=None
    )
    exit_code = main(["https://example.com"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "unscored" in captured.out
    assert "invalid_url" in captured.out


@patch("page_health.cli.score_page")
def test_scored_page_prints_score_and_returns_zero(mock_score, capsys):
    mock_score.return_value = make_result(score=87)
    exit_code = main(["https://example.com"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "87/100" in captured.out


@patch("page_health.cli.score_page")
def test_redirect_prints_final_url(mock_score, capsys):
    mock_score.return_value = make_result(url="https://example.com", final_url="https://example.com/home")
    main(["https://example.com"])
    captured = capsys.readouterr()
    assert "https://example.com/home" in captured.out


@patch("page_health.cli.score_page")
def test_no_fixes_section_when_fixes_list_is_empty(mock_score, capsys):
    mock_score.return_value = make_result(fixes=[])
    main(["https://example.com"])
    captured = capsys.readouterr()
    assert "Top fixes" not in captured.out
