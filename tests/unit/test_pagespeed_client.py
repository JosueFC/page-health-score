"""Tests for page_health.pagespeed_client.

All requests.get calls are mocked -- no test here makes a real network call
to PageSpeed Insights. Covers: successful parsing, throttling between calls,
retry-exactly-once on 429 (not zero, not two-plus), non-429 failures NOT
being retried, connection errors NOT being retried, and every failure path
surfacing PSI's actual status code / response detail rather than a generic
message.
"""

from unittest.mock import Mock, call, patch

import pytest
import requests

from page_health.pagespeed_client import (
    DEFAULT_MAX_ATTEMPTS,
    MIN_SECONDS_BETWEEN_CALLS,
    fetch_pagespeed_data,
)
import page_health.pagespeed_client as pagespeed_client_module


def _psi_response(status_code=200, seo=0.95, performance=0.72, text="", json_data=None):
    response = Mock()
    response.status_code = status_code
    response.text = text
    if json_data is not None:
        response.json.return_value = json_data
    else:
        response.json.return_value = {
            "lighthouseResult": {
                "categories": {
                    "seo": {"score": seo},
                    "performance": {"score": performance},
                }
            }
        }
    return response


@pytest.fixture(autouse=True)
def reset_throttle_state():
    """Each test gets a clean throttle clock so tests don't leak timing
    state into each other."""
    pagespeed_client_module._last_call_time = None
    yield
    pagespeed_client_module._last_call_time = None


# --- successful fetch -------------------------------------------------


@patch("page_health.pagespeed_client.requests.get")
def test_successful_fetch_parses_scores_as_0_100(mock_get):
    mock_get.return_value = _psi_response(seo=0.95, performance=0.72)

    result = fetch_pagespeed_data("https://example.com/page")

    assert result.seo_score == 95
    assert result.performance_score == 72
    assert result.error is None
    assert result.status_code == 200


@patch("page_health.pagespeed_client.requests.get")
def test_only_one_request_made_on_success(mock_get):
    mock_get.return_value = _psi_response()
    fetch_pagespeed_data("https://example.com/page")
    assert mock_get.call_count == 1


# --- throttle ------------------------------------------------------------


@patch("page_health.pagespeed_client.time.sleep")
@patch("page_health.pagespeed_client.time.monotonic")
@patch("page_health.pagespeed_client.requests.get")
def test_second_call_sleeps_for_remaining_throttle_window(mock_get, mock_monotonic, mock_sleep):
    mock_get.return_value = _psi_response()
    # First call happens at t=0 (one monotonic() read, to record last-call
    # time). Second call at t=0.5 -- reads monotonic() once to measure
    # elapsed (should sleep for the remaining window), then again to record
    # its own last-call time.
    mock_monotonic.side_effect = [0.0, 0.5, 1.1]

    fetch_pagespeed_data("https://example.com/page-a")
    fetch_pagespeed_data("https://example.com/page-b")

    mock_sleep.assert_called_once()
    slept_for = mock_sleep.call_args[0][0]
    assert slept_for == pytest.approx(MIN_SECONDS_BETWEEN_CALLS - 0.5)


@patch("page_health.pagespeed_client.time.sleep")
@patch("page_health.pagespeed_client.requests.get")
def test_first_call_does_not_sleep(mock_get, mock_sleep):
    mock_get.return_value = _psi_response()
    fetch_pagespeed_data("https://example.com/page")
    mock_sleep.assert_not_called()


# --- retry on 429, capped at exactly one --------------------------------


@patch("page_health.pagespeed_client.time.sleep")
@patch("page_health.pagespeed_client.requests.get")
def test_429_then_success_retries_exactly_once(mock_get, mock_sleep):
    mock_get.side_effect = [_psi_response(status_code=429, text="rate limited"), _psi_response()]

    result = fetch_pagespeed_data("https://example.com/page")

    assert mock_get.call_count == 2  # initial attempt + exactly one retry
    assert result.error is None
    assert result.seo_score == 95


def test_max_attempts_is_exactly_two():
    """One retry after the initial attempt -- not open-ended, not zero."""
    assert DEFAULT_MAX_ATTEMPTS == 2


@patch("page_health.pagespeed_client.time.sleep")
@patch("page_health.pagespeed_client.requests.get")
def test_429_twice_exhausts_retry_and_fails_loudly(mock_get, mock_sleep):
    mock_get.side_effect = [
        _psi_response(status_code=429, text="rate limited (1)"),
        _psi_response(status_code=429, text="rate limited (2)"),
    ]

    result = fetch_pagespeed_data("https://example.com/page")

    assert mock_get.call_count == 2  # exactly two attempts total, no third
    assert result.seo_score is None
    assert result.status_code == 429
    # the failure must be loud and specific -- the real status code and
    # body, not a generic "gave up after retries" message
    assert "429" in result.error
    assert "rate limited (2)" in result.error


# --- non-429 failures are NOT retried -------------------------------------


@patch("page_health.pagespeed_client.time.sleep")
@patch("page_health.pagespeed_client.requests.get")
def test_403_is_not_retried_and_error_includes_status_and_body(mock_get, mock_sleep):
    mock_get.return_value = _psi_response(status_code=403, text="API key invalid")

    result = fetch_pagespeed_data("https://example.com/page", api_key="bad-key")

    assert mock_get.call_count == 1  # no retry for a non-429 failure
    assert result.status_code == 403
    assert "403" in result.error
    assert "API key invalid" in result.error


@patch("page_health.pagespeed_client.time.sleep")
@patch("page_health.pagespeed_client.requests.get")
def test_500_is_not_retried(mock_get, mock_sleep):
    mock_get.return_value = _psi_response(status_code=500, text="internal error")
    result = fetch_pagespeed_data("https://example.com/page")
    assert mock_get.call_count == 1
    assert result.status_code == 500
    assert "500" in result.error


# --- connection-level failures are NOT retried ----------------------------


@patch("page_health.pagespeed_client.requests.get")
def test_connection_error_is_not_retried(mock_get):
    mock_get.side_effect = requests.ConnectionError("Connection refused")

    result = fetch_pagespeed_data("https://example.com/page")

    assert mock_get.call_count == 1
    assert result.seo_score is None
    assert result.status_code is None
    assert "Connection refused" in result.error


@patch("page_health.pagespeed_client.requests.get")
def test_timeout_is_not_retried(mock_get):
    mock_get.side_effect = requests.Timeout("Request timed out")
    result = fetch_pagespeed_data("https://example.com/page")
    assert mock_get.call_count == 1
    assert "timed out" in result.error


# --- malformed response shape -----------------------------------------


@patch("page_health.pagespeed_client.requests.get")
def test_malformed_json_shape_surfaces_specific_error(mock_get):
    mock_get.return_value = _psi_response(json_data={"unexpected": "shape"})

    result = fetch_pagespeed_data("https://example.com/page")

    assert result.seo_score is None
    assert result.performance_score is None
    assert result.error is not None
    assert result.status_code == 200


# --- API key handling ---------------------------------------------------


@patch("page_health.pagespeed_client.requests.get")
def test_explicit_api_key_is_passed_in_params(mock_get):
    mock_get.return_value = _psi_response()
    fetch_pagespeed_data("https://example.com/page", api_key="my-key")
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["key"] == "my-key"


@patch("page_health.pagespeed_client.requests.get")
def test_env_var_api_key_used_when_not_passed_explicitly(mock_get, monkeypatch):
    monkeypatch.setenv("PAGESPEED_API_KEY", "env-key")
    mock_get.return_value = _psi_response()
    fetch_pagespeed_data("https://example.com/page")
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["key"] == "env-key"


@patch("page_health.pagespeed_client.requests.get")
def test_no_api_key_omits_key_param(mock_get, monkeypatch):
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    mock_get.return_value = _psi_response()
    fetch_pagespeed_data("https://example.com/page")
    _, kwargs = mock_get.call_args
    assert "key" not in kwargs["params"]
