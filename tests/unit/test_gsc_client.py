"""Tests for page_health.gsc_client.

All auth and network calls are mocked -- no test makes a real call to
Google Search Console. Covers: missing credentials, missing site URL, auth
failure, request failure, successful parsing (impressions/clicks/CTR/
distinct-query-count), and that every failure path carries a specific,
non-generic reason.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
import requests

from page_health.gsc_client import fetch_search_console_data


def _psi_like_response(status_code=200, json_data=None, text=""):
    response = Mock()
    response.status_code = status_code
    response.text = text
    response.json.return_value = json_data if json_data is not None else {"rows": []}
    return response


# --- missing configuration -------------------------------------------


def test_missing_credentials_path_returns_specific_error(monkeypatch):
    monkeypatch.delenv("GSC_CREDENTIALS_PATH", raising=False)
    result = fetch_search_console_data("https://example.com/page", site_url="https://example.com/")
    assert result.error is not None
    assert "credentials" in result.error.lower()
    assert result.impressions is None


def test_missing_site_url_returns_specific_error(monkeypatch):
    monkeypatch.delenv("GSC_SITE_URL", raising=False)
    result = fetch_search_console_data(
        "https://example.com/page", credentials_path="/fake/creds.json"
    )
    assert result.error is not None
    assert "site url" in result.error.lower() or "site_url" in result.error.lower()


def test_env_var_credentials_and_site_url_used_when_not_passed_explicitly(monkeypatch):
    monkeypatch.setenv("GSC_CREDENTIALS_PATH", "/env/creds.json")
    monkeypatch.delenv("GSC_SITE_URL", raising=False)
    # site_url still missing -> should fail on that, proving credentials_path
    # was picked up from the env var rather than failing on credentials first
    result = fetch_search_console_data("https://example.com/page")
    assert "site url" in result.error.lower() or "site_url" in result.error.lower()


# --- auth failure ---------------------------------------------------------


@patch("page_health.gsc_client._load_credentials")
def test_auth_failure_returns_specific_error(mock_load_creds):
    mock_load_creds.side_effect = RuntimeError("invalid key file")
    result = fetch_search_console_data(
        "https://example.com/page",
        site_url="https://example.com/",
        credentials_path="/fake/creds.json",
    )
    assert result.error is not None
    assert "authenticate" in result.error.lower()
    assert "invalid key file" in result.error


# --- successful fetch ----------------------------------------------------


@patch("page_health.gsc_client.requests.post")
@patch("page_health.gsc_client._get_access_token")
@patch("page_health.gsc_client._load_credentials")
def test_successful_fetch_aggregates_rows(mock_load_creds, mock_token, mock_post):
    mock_load_creds.return_value = MagicMock()
    mock_token.return_value = "fake-token"
    mock_post.return_value = _psi_like_response(
        json_data={
            "rows": [
                {"keys": ["query one"], "clicks": 4, "impressions": 100},
                {"keys": ["query two"], "clicks": 1, "impressions": 50},
            ]
        }
    )

    result = fetch_search_console_data(
        "https://example.com/page",
        site_url="https://example.com/",
        credentials_path="/fake/creds.json",
    )

    assert result.error is None
    assert result.impressions == 150
    assert result.clicks == 5
    assert result.ctr == pytest.approx(5 / 150)
    assert result.distinct_query_count == 2


@patch("page_health.gsc_client.requests.post")
@patch("page_health.gsc_client._get_access_token")
@patch("page_health.gsc_client._load_credentials")
def test_no_rows_returns_zeroed_but_available_result(mock_load_creds, mock_token, mock_post):
    mock_load_creds.return_value = MagicMock()
    mock_token.return_value = "fake-token"
    mock_post.return_value = _psi_like_response(json_data={"rows": []})

    result = fetch_search_console_data(
        "https://example.com/page",
        site_url="https://example.com/",
        credentials_path="/fake/creds.json",
    )

    assert result.error is None
    assert result.impressions == 0
    assert result.ctr == 0.0
    assert result.distinct_query_count == 0


# --- request failure ------------------------------------------------------


@patch("page_health.gsc_client.requests.post")
@patch("page_health.gsc_client._get_access_token")
@patch("page_health.gsc_client._load_credentials")
def test_non_200_response_returns_specific_error(mock_load_creds, mock_token, mock_post):
    mock_load_creds.return_value = MagicMock()
    mock_token.return_value = "fake-token"
    mock_post.return_value = _psi_like_response(status_code=403, text="property not verified for this account")

    result = fetch_search_console_data(
        "https://example.com/page",
        site_url="https://example.com/",
        credentials_path="/fake/creds.json",
    )

    assert result.error is not None
    assert "403" in result.error
    assert "property not verified" in result.error


@patch("page_health.gsc_client.requests.post")
@patch("page_health.gsc_client._get_access_token")
@patch("page_health.gsc_client._load_credentials")
def test_connection_error_returns_specific_error(mock_load_creds, mock_token, mock_post):
    mock_load_creds.return_value = MagicMock()
    mock_token.return_value = "fake-token"
    mock_post.side_effect = requests.ConnectionError("Connection refused")

    result = fetch_search_console_data(
        "https://example.com/page",
        site_url="https://example.com/",
        credentials_path="/fake/creds.json",
    )

    assert "Connection refused" in result.error


# --- window ---------------------------------------------------------------


@patch("page_health.gsc_client.requests.post")
@patch("page_health.gsc_client._get_access_token")
@patch("page_health.gsc_client._load_credentials")
def test_default_window_is_90_days(mock_load_creds, mock_token, mock_post):
    from datetime import date, timedelta

    mock_load_creds.return_value = MagicMock()
    mock_token.return_value = "fake-token"
    mock_post.return_value = _psi_like_response(json_data={"rows": []})

    fetch_search_console_data(
        "https://example.com/page",
        site_url="https://example.com/",
        credentials_path="/fake/creds.json",
    )

    _, kwargs = mock_post.call_args
    body = kwargs["json"]
    start = date.fromisoformat(body["startDate"])
    end = date.fromisoformat(body["endDate"])
    assert (end - start).days == 90
