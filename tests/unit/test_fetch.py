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
    assert result.error_category == "connection_failed"


@patch("page_health.fetch.requests.get")
def test_missing_scheme_url_is_categorized_as_invalid_url_not_connection_failure(mock_get):
    """A scheme-less URL (e.g. 'weeklift.com') never reaches the network at
    all -- requests raises MissingSchema before attempting a connection.
    That's a different fact from a genuine connection failure and must be
    categorized separately, so assess_confidence() can report an accurate
    reason rather than collapsing both into 'no_http_response'."""
    mock_get.side_effect = requests.exceptions.MissingSchema(
        "Invalid URL 'weeklift.com': No scheme supplied. Perhaps you meant https://weeklift.com?"
    )

    result = fetch_page("weeklift.com")

    assert result.html is None
    assert result.error_category == "invalid_url"
    assert "No scheme supplied" in result.error


@patch("page_health.fetch.requests.get")
def test_malformed_url_is_categorized_as_invalid_url(mock_get):
    mock_get.side_effect = requests.exceptions.InvalidURL("Invalid URL")
    result = fetch_page("https://[invalid")
    assert result.error_category == "invalid_url"


@patch("page_health.fetch.requests.get")
def test_invalid_schema_is_categorized_as_invalid_url(mock_get):
    """Regression test: the first version of this fix only caught
    MissingSchema and InvalidURL by name and missed InvalidSchema (raised
    for a recognized-but-unsupported scheme, e.g. a typo'd 'ht!tp://').
    fetch_page() now catches on ValueError generally -- every one of
    requests' own URL-parsing exceptions subclasses it -- rather than
    naming exception classes one at a time."""
    mock_get.side_effect = requests.exceptions.InvalidSchema(
        "No connection adapters were found for 'ht!tp://example.com'"
    )
    result = fetch_page("ht!tp://example.com")
    assert result.error_category == "invalid_url"
    assert result.error_category != "connection_failed"


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


# --- fetch identity (post-Day-6: real site returned 406 under requests'
# default UA, 200 under a browser UA -- see module docstring for the fix) --


@patch("page_health.fetch.requests.get")
def test_default_user_agent_is_browser_like_not_pythons_default(mock_get, monkeypatch):
    monkeypatch.delenv("USER_AGENT", raising=False)
    mock_get.return_value = _mock_response()

    fetch_page("https://example.com/page")

    _, kwargs = mock_get.call_args
    sent_ua = kwargs["headers"]["User-Agent"]
    assert "python-requests" not in sent_ua
    assert "Mozilla" in sent_ua  # every mainstream browser UA includes this token


@patch("page_health.fetch.requests.get")
def test_user_agent_env_var_overrides_the_default(mock_get, monkeypatch):
    monkeypatch.setenv("USER_AGENT", "MyCustomBot/1.0")
    mock_get.return_value = _mock_response()

    fetch_page("https://example.com/page")

    _, kwargs = mock_get.call_args
    assert kwargs["headers"]["User-Agent"] == "MyCustomBot/1.0"


@patch("page_health.fetch.requests.get")
def test_accept_and_accept_language_headers_are_always_sent(mock_get, monkeypatch):
    monkeypatch.delenv("USER_AGENT", raising=False)
    mock_get.return_value = _mock_response()

    fetch_page("https://example.com/page")

    _, kwargs = mock_get.call_args
    assert "Accept" in kwargs["headers"]
    assert "Accept-Language" in kwargs["headers"]


@patch("page_health.fetch.requests.get")
def test_accept_headers_are_not_configurable_via_env_var(mock_get, monkeypatch):
    """Decision: Accept/Accept-Language are hardcoded, bundled with
    whichever UA is active -- not independently configurable. Setting an
    unrelated env var should have no effect on them."""
    monkeypatch.setenv("USER_AGENT", "MyCustomBot/1.0")
    mock_get.return_value = _mock_response()

    fetch_page("https://example.com/page")

    _, kwargs = mock_get.call_args
    from page_health.fetch import DEFAULT_ACCEPT_HEADER, DEFAULT_ACCEPT_LANGUAGE_HEADER

    assert kwargs["headers"]["Accept"] == DEFAULT_ACCEPT_HEADER
    assert kwargs["headers"]["Accept-Language"] == DEFAULT_ACCEPT_LANGUAGE_HEADER


@patch("page_health.fetch.requests.get")
def test_request_user_agent_recorded_on_successful_fetch(mock_get, monkeypatch):
    monkeypatch.setenv("USER_AGENT", "MyCustomBot/1.0")
    mock_get.return_value = _mock_response()

    result = fetch_page("https://example.com/page")

    assert result.request_user_agent == "MyCustomBot/1.0"


@patch("page_health.fetch.requests.get")
def test_request_user_agent_recorded_even_on_connection_failure(mock_get, monkeypatch):
    """Safeguard requirement: the UA actually used is always recorded, on
    every path -- including failures -- never just used internally and
    discarded."""
    monkeypatch.setenv("USER_AGENT", "MyCustomBot/1.0")
    mock_get.side_effect = requests.ConnectionError("Connection refused")

    result = fetch_page("https://example.com/page")

    assert result.request_user_agent == "MyCustomBot/1.0"


@patch("page_health.fetch.requests.get")
def test_request_user_agent_recorded_even_on_invalid_url(mock_get, monkeypatch):
    monkeypatch.setenv("USER_AGENT", "MyCustomBot/1.0")
    mock_get.side_effect = requests.exceptions.MissingSchema("No scheme supplied")

    result = fetch_page("weeklift.com")

    assert result.request_user_agent == "MyCustomBot/1.0"