"""Tests for page_health.fetch.

fetch.py does I/O, so these tests mock requests.get rather than hitting the
network. Scope here is deliberately narrow -- just what Crawlability needs
(redirect following, status/headers/html capture, connection-failure
handling). The full confidence-tier model isn't implemented yet (Day 2), so
there's nothing to test for content-type or malformed-body cases here.
"""

from unittest.mock import Mock, patch

import requests

from page_health.fetch import FetchResult, fetch_page


def _mock_response(status_code=200, url="https://example.com/page", headers=None, text="<html></html>"):
    response = Mock()
    response.status_code = status_code
    response.url = url
    response.headers = headers or {}
    response.text = text
    return response


@patch("page_health.fetch.requests.get")
def test_successful_fetch_returns_populated_result(mock_get):
    mock_get.return_value = _mock_response(status_code=200, text="<html><body>hi</body></html>")

    result = fetch_page("https://example.com/page")

    assert isinstance(result, FetchResult)
    assert result.status_code == 200
    assert result.html == "<html><body>hi</body></html>"
    assert result.error is None
    assert result.succeeded is True


@patch("page_health.fetch.requests.get")
def test_redirect_records_original_and_final_url(mock_get):
    mock_get.return_value = _mock_response(url="https://example.com/final-page")

    result = fetch_page("https://example.com/original-page")

    assert result.original_url == "https://example.com/original-page"
    assert result.final_url == "https://example.com/final-page"


@patch("page_health.fetch.requests.get")
def test_connection_failure_sets_error_and_no_html(mock_get):
    mock_get.side_effect = requests.ConnectionError("Connection refused")

    result = fetch_page("https://example.com/page")

    assert result.html is None
    assert result.status_code is None
    assert result.error == "Connection refused"
    assert result.succeeded is False


@patch("page_health.fetch.requests.get")
def test_timeout_sets_error_and_no_html(mock_get):
    mock_get.side_effect = requests.Timeout("Request timed out")

    result = fetch_page("https://example.com/page")

    assert result.html is None
    assert result.error == "Request timed out"
    assert result.succeeded is False


@patch("page_health.fetch.requests.get")
def test_follows_redirects_is_requested(mock_get):
    mock_get.return_value = _mock_response()

    fetch_page("https://example.com/page")

    _, kwargs = mock_get.call_args
    assert kwargs.get("allow_redirects") is True


@patch("page_health.fetch.requests.get")
def test_headers_are_captured(mock_get):
    mock_get.return_value = _mock_response(headers={"X-Robots-Tag": "noindex"})

    result = fetch_page("https://example.com/page")

    assert result.headers.get("X-Robots-Tag") == "noindex"
