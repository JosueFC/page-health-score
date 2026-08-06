"""Google Search Console client. All I/O for Search Evidence's external
dependency lives here -- and per SCOPE_OF_WORK.md §6, this is the ONLY file
in the entire codebase that touches GSC. No other component imports this
module; that boundary is structural, not just a convention, and enforces
§6's "Search-Evidence-is-the-only-GSC-touching-component" rationale for why
this is a separate repo from weeklift in the first place.

Auth model (Day 5 sign-off): a GSC service account, added as a property
user in Search Console -- not "no auth, simplified," but the actually
correct model for a single-operator local CLI. Search Console natively
supports a service account as a collaborator, which is simpler and more
appropriate here than replicating weeklift's own per-customer OAuth
refresh-token flow would be. That per-customer OAuth flow is a real,
separate need for weeklift's eventual integration (§11) -- this client
deliberately does not attempt to anticipate it; that's a gap for whoever
picks up §11's integration trigger, not something to design around now.

Credential path: GSC_CREDENTIALS_PATH env var (or passed explicitly),
mirroring how pagespeed_client.py reads PAGESPEED_API_KEY. Site URL (the
verified Search Console property -- e.g. "https://example.com/" or
"sc-domain:example.com") comes from GSC_SITE_URL or an explicit param,
since it isn't necessarily derivable from the page URL being scored (a
verified property can be domain-level while the page being scored is one
URL within it).

Window: trailing 90 days (Day 5 sign-off), matching the same convention
weeklift's striking-distance design already established for reducing
short-term noise in position/traffic signals, rather than an unstated or
arbitrarily different number.
"""

import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional
from urllib.parse import quote

import requests

try:
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2 import service_account
except ImportError:  # pragma: no cover -- exercised only if google-auth isn't installed
    service_account = None
    GoogleAuthRequest = None

SEARCH_CONSOLE_API_BASE = "https://www.googleapis.com/webmasters/v3/sites"
SEARCH_CONSOLE_SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# Trailing window in days -- see module docstring. Named, documented,
# explicitly tunable like every other such constant in this codebase, but
# backed by an actual precedent (weeklift's striking-distance design) rather
# than a guess.
WINDOW_DAYS = 90

# GSC data typically has a 2-3 day processing lag; scoring "yesterday" as
# the end date would frequently return incomplete data for the most recent
# days. Back off a few days to stay inside data GSC has actually finalized.
DATA_LAG_DAYS = 3


@dataclass
class GSCResult:
    """Result of a Search Console query for a single page.

    error is None on success. On failure it carries the actual reason
    (missing credentials, missing site URL, auth failure, or the API's own
    error detail) -- same "loud and specific, never generic" principle used
    in pagespeed_client.py.
    """

    impressions: Optional[int]
    clicks: Optional[int]
    ctr: Optional[float]  # total clicks / total impressions over the window
    distinct_query_count: Optional[int]
    error: Optional[str]


def _load_credentials(credentials_path: str):
    if service_account is None:
        raise RuntimeError(
            "google-auth is not installed; it's required for GSC access "
            "(see requirements.txt)."
        )
    return service_account.Credentials.from_service_account_file(
        credentials_path, scopes=SEARCH_CONSOLE_SCOPES
    )


def _get_access_token(credentials) -> str:
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())
    return credentials.token


def fetch_search_console_data(
    page_url: str,
    site_url: Optional[str] = None,
    credentials_path: Optional[str] = None,
    window_days: int = WINDOW_DAYS,
    timeout: float = 30.0,
) -> GSCResult:
    """Fetch impressions/clicks/CTR/query-diversity for a single page over
    a trailing window.

    Returns a GSCResult with error set (and all numeric fields None) if:
    credentials aren't configured, the site URL isn't configured, auth
    fails, or the API call itself fails. None of these are the page's
    fault -- score_search_evidence() treats all of them as "GSC
    unavailable" and rescales the whole component out rather than scoring
    against data that was never actually retrieved.
    """
    if credentials_path is None:
        credentials_path = os.environ.get("GSC_CREDENTIALS_PATH")
    if not credentials_path:
        return GSCResult(
            impressions=None,
            clicks=None,
            ctr=None,
            distinct_query_count=None,
            error="No GSC credentials configured (GSC_CREDENTIALS_PATH not set)",
        )

    if site_url is None:
        site_url = os.environ.get("GSC_SITE_URL")
    if not site_url:
        return GSCResult(
            impressions=None,
            clicks=None,
            ctr=None,
            distinct_query_count=None,
            error="No GSC site URL configured (GSC_SITE_URL not set)",
        )

    try:
        credentials = _load_credentials(credentials_path)
        token = _get_access_token(credentials)
    except Exception as exc:  # noqa: BLE001 -- surfacing the real auth failure, not narrowing it
        return GSCResult(
            impressions=None,
            clicks=None,
            ctr=None,
            distinct_query_count=None,
            error=f"Failed to authenticate with Google Search Console: {exc}",
        )

    end_date = date.today() - timedelta(days=DATA_LAG_DAYS)
    start_date = end_date - timedelta(days=window_days)

    request_body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["query"],
        "dimensionFilterGroups": [
            {"filters": [{"dimension": "page", "operator": "equals", "expression": page_url}]}
        ],
        "rowLimit": 25000,
    }

    endpoint = f"{SEARCH_CONSOLE_API_BASE}/{quote(site_url, safe='')}/searchAnalytics/query"
    headers = {"Authorization": f"Bearer {token}"}

    try:
        response = requests.post(endpoint, headers=headers, json=request_body, timeout=timeout)
    except requests.RequestException as exc:
        return GSCResult(
            impressions=None,
            clicks=None,
            ctr=None,
            distinct_query_count=None,
            error=str(exc),
        )

    if response.status_code != 200:
        return GSCResult(
            impressions=None,
            clicks=None,
            ctr=None,
            distinct_query_count=None,
            error=(
                f"Search Console API returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            ),
        )

    try:
        data = response.json()
    except ValueError as exc:
        return GSCResult(
            impressions=None,
            clicks=None,
            ctr=None,
            distinct_query_count=None,
            error=f"Search Console API returned an unparseable response: {exc}",
        )

    rows = data.get("rows", [])
    total_impressions = sum(row.get("impressions", 0) for row in rows)
    total_clicks = sum(row.get("clicks", 0) for row in rows)
    ctr = (total_clicks / total_impressions) if total_impressions > 0 else 0.0

    return GSCResult(
        impressions=total_impressions,
        clicks=total_clicks,
        ctr=ctr,
        distinct_query_count=len(rows),
        error=None,
    )
