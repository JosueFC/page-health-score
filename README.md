# Page Health Score

A standalone, zero-cost, rules-based 0–100 SEO health scorer for a single
web page. Deterministic, no ML, no LLM calls. Evaluates technical SEO and
content quality against a single page's HTML, PageSpeed Insights, and
(optionally) Google Search Console data.

**This is a technical/SEO hygiene score. It is not, and must never be
marketed or designed as, a GEO (Generative Engine Optimization) or
AI-citation-likelihood predictor.** See "What this is not," below — this
constraint is permanent, not a v1 simplification.

Full build history and every scoring-rule decision (with the trade-offs
considered, not just the final call) is in `DECISIONS_LOG.md`. This README
is the current-state reference; that file is the "why we got here."

---

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest                          # 153 tests, all external I/O mocked
python -m page_health https://example.com
```

No credentials are required to run the tool. Without them, PageSpeed
Insights and Search Console data simply aren't available, and the
corresponding components rescale out of the score rather than the run
failing — see "Optional external data," below.

---

## What this is not

This tool stands on its own as a legitimate SEO hygiene metric. A page can
score well here and that claim is defensible on its own terms, without any
implied claim about how likely an LLM is to cite it. This applies to every
piece of copy, every field name, and every future UI surface this tool
produces — not just the scoring logic.

---

## The scoring model

100 nominal points across five components, of which 90 are built and
scored in v1:

| Component | Points | What it checks |
|---|---|---|
| Crawlability | 20 | HTTP status, noindex, canonical self-reference |
| Content Structure | 20 | Title, H1, heading hierarchy, body copy, lists/tables/FAQ |
| Structured Data | 15 | JSON-LD presence, validity, type specificity, required properties |
| Technical Quality | 20 | PageSpeed SEO/performance scores, image alt coverage, internal linking |
| Search Evidence | 15 | Google Search Console impressions, CTR, query diversity |
| *(reserved)* | 10 | Buffer for future content-clarity signals — **unbuilt and unscored in v1**, see below |

The final displayed score is **always projected onto a 0–100 scale**,
computed once in `score.py`:

```
score = round(achieved_points / achievable_max_points * 100)
```

This isn't only for pages where a component is unavailable — the reserved
10-point buffer is permanently unbuilt, so `achievable_max_points` is at
most 90 on every page, always. Projection is what keeps "a 0–100 score"
literally true across every page this tool scores. The raw, unprojected
`achieved_points` / `achievable_max_points` are still carried in full on
`PageScoreResult` for anyone who wants the number this was computed from.

### Crawlability (20 points)

| Signal | Points | Rule |
|---|---|---|
| HTTP 200 (final, post-redirect) | 10 | Binary |
| No noindex (meta robots OR `X-Robots-Tag`, either fails) | 6 | Binary |
| Canonical present and self-referencing | 4 | Binary |

Malformed canonical (empty href, unresolvable, multiple conflicting tags)
and a resolvable-but-points-elsewhere canonical both score 0/4 — but are
kept as distinguishable reasons in raw diagnostics, since they're different
fixes for a reader even at an identical score.

### Content Structure (20 points)

| Signal | Points | Rule |
|---|---|---|
| Title present | 4 | Binary |
| Exactly one H1 | 4 | Binary — zero *or* multiple both score 0 |
| Heading hierarchy | 4 | 2 pts: has any H2. 2 pts: no H3 before an H2, no H4+ used at all — *requires* has-H2 as a precondition |
| Body copy sufficiency | 4 | Visible word count ≥ `MIN_VISIBLE_TEXT_WORDS` (150) — shares this constant with the confidence detector below |
| Lists/tables/FAQ block | 4 | Any of: list with ≥3 `<li>`, table with ≥3 `<tr>` total, or ≥2 question-headings each followed by a paragraph |

### Structured Data (15 points, staged/cascading)

Each signal gates the next — a page with no JSON-LD scores 0/15 outright,
not partial credit for signals it never had a chance to fail.

| Signal | Points | Rule |
|---|---|---|
| JSON-LD present | 3 | Any `<script type="application/ld+json">` with content |
| Valid parsing | 4 | **Any** block parses (not "all must parse") — but broken-block count/detail is always in raw output regardless of score |
| Type specificity | 4 | `@type` present and not in the generic denylist (`Thing`, `WebPage`, `WebSite`, `CreativeWork` — `Article` is *not* denylisted) |
| Required properties | 4 | Type-specific required fields present and non-empty. **Unrecognized type → this page's `max_points` rescales from 15 to 11**, not scored 0 or 4 |

### Technical Quality (20 points, 10 of which depend on PageSpeed Insights)

| Signal | Points | Rule |
|---|---|---|
| PSI SEO score | 5 | Binary gate at ≥ 90 |
| PSI performance score | 5 | Binary gate at ≥ 90 |
| Image alt coverage | 5 | Coverage ≥ 90%. Zero images on the page → automatic pass |
| Internal linking | 5 | ≥ 3 same-domain links (excludes anchors, `mailto:`, `tel:`) |

**If PSI is unavailable** (no key, quota exhausted, outage, or PSI fails to
analyze the page) — the two PSI-derived signals rescale out together as one
10-point block. This page's `max_points` for the component drops from 20 to
10; alt coverage and internal linking are unaffected.

### Search Evidence (15 points, requires GSC)

Impressions gates CTR and query diversity — the same signal doing double
duty as the floor, rather than a separately-invented threshold. A page
below the impressions bar has too little traffic for CTR/diversity to mean
anything.

| Signal | Points | Rule |
|---|---|---|
| Impressions | 5 | ≥ `IMPRESSIONS_THRESHOLD` (100) over a trailing 90-day window |
| CTR | 5 | *Gated by Impressions.* ≥ `CTR_THRESHOLD` (2%) |
| Query diversity | 5 | *Gated by Impressions.* ≥ `QUERY_DIVERSITY_THRESHOLD` (5 distinct queries) |

**If GSC is unavailable** (no credentials, no site URL, auth failure, API
failure) — the **entire 15-point component** rescales to `0/0`, not scored
0/15. This is the default state for every page scored before WeekLift has
paying users (§11) — not a rare edge case, the common case for this
component's entire pre-integration life.

---

## The confidence model

Applied once per page, to the whole score — not per component.

| Tier | Meaning |
|---|---|
| **Unscored** | No HTTP response at all, connection failure, or non-HTML content-type. Score is `None`, never a misleadingly low number. |
| **Scored, low-confidence** | Page fetched and parsed, but one of two detectors fired (below). Score is still computed from whatever was extractable. |
| **Scored, high-confidence** | Complete, well-formed HTML, reasonable text length, no caveats. |

Two detectors, both with a hard wording rule: **reason codes report what
was observed, never what's inferred.**

- `closing_html_tag_not_found` — raw response lacks a case-insensitive
  `</html>`. Must never say "truncated" — a legitimately sloppy-but-complete
  page looks identical to this check.
- `low_visible_text_word_count` — extracted text below
  `MIN_VISIBLE_TEXT_WORDS` despite well-formed HTML. Must never say
  "JS-rendered" or "SPA detected" — a legitimately short page (pricing,
  contact, landing page) looks identical to this check.

Both guards are enforced by permanent unit tests asserting the reason
strings never contain the disallowed substrings, not just by docstring
promise.

---

## Known, accepted limitations (v1)

- **Cannot execute JavaScript.** `requests` + BeautifulSoup only.
  Client-side-rendered content is invisible to this tool — mitigated, not
  solved, by the low-visible-text-word-count detector above, honestly
  labeled as a possibility, not a diagnosis.
- **No framework fingerprinting**, deliberately. Not attempting to detect
  *why* a page might be JS-rendered — that's a fragile, ongoing arms race
  against every framework's conventions. The blunter word-count signal
  degrades more gracefully.
- **PageSpeed Insights rate limits are real** even at zero dollar cost —
  roughly 1 req/sec informally enforced unauthenticated, plus a daily quota
  with a key. Handled via self-throttling and exactly one retry on 429 (see
  `pagespeed_client.py`) — never an open-ended retry loop, and every final
  failure surfaces PSI's actual status code and response detail.
- **Truncation vs. malformed-but-complete HTML** cannot be distinguished by
  the closing-tag check alone. Accepted as a v1 simplification; the wording
  constraint above is the mitigation, not a fix.
- **Structured Data's required-properties type coverage is a small,
  hardcoded, growing map** (`Organization`, `LocalBusiness`, `Product`,
  `Article`/`BlogPosting`/`NewsArticle`, `BreadcrumbList`, `FAQPage`). An
  unrecognized-but-specific type isn't guessed at either direction — see
  the rescale-ceiling behavior above.
- **GSC's v1 auth model is a single operator-owned service account**, the
  correct model for a single-operator local CLI — not a stand-in for
  WeekLift's eventual need for per-customer GSC OAuth, which is a genuinely
  different, currently-unbuilt auth flow (see §11 in `SCOPE_OF_WORK.md`).
- **Basic bot-detection can produce a false Crawlability failure.**
  Observed directly on a real site: HTTP 406 under `requests`' default
  User-Agent, HTTP 200 under a browser UA. Mitigated with a browser-like
  default `User-Agent` and matching `Accept`/`Accept-Language` headers,
  operator-overridable via `USER_AGENT` — not solved: a WAF can still
  fingerprint on other request characteristics this tool doesn't control,
  and this tool cannot distinguish "genuinely blocked from all crawlers"
  from "blocked by a fingerprint check a real crawler happens to pass."
  See `DECISIONS_LOG.md` decision #23 for the full reasoning, including why
  Googlebot impersonation was rejected as the *default* but remains an
  unsupported operator opt-in via `USER_AGENT`.

---

## Optional external data

| Env var | Used by | Behavior when unset |
|---|---|---|
| `USER_AGENT` | `fetch.py` | Uses a browser-like default UA (not `requests`' default identity) |
| `PAGESPEED_API_KEY` | `pagespeed_client.py` | Unauthenticated PSI calls (lower daily quota) |
| `GSC_CREDENTIALS_PATH` | `gsc_client.py` | Search Evidence rescales to 0/0 |
| `GSC_SITE_URL` | `gsc_client.py` | Search Evidence rescales to 0/0 |

None are required. `python -m page_health <url>` runs fully with zero
configuration; Technical Quality and Search Evidence just score against a
smaller denominator when their data sources aren't configured.

---

## Tunable / not-yet-final values

Every value below is a documented starting point, not a researched or
permanently-settled number. All are explicitly pending recalibration once
real customer pages are available — none should be treated as load-bearing
without checking here first.

| Constant | Location | Value | Note |
|---|---|---|---|
| Cross-component point weights | `SCOPE_OF_WORK.md` §3 | 20/20/15/20/15/10 | Not stress-tested against real pages |
| Crawlability's internal split | `crawlability.py` | 10/6/4 | Confirmed, not tunable — the one exception |
| `X-Robots-Tag` user-agent scoping | `crawlability.py` | any token fails | Conservative default, may prove too blunt |
| Content Structure's internal split | `content.py` | 4/4/4/4/4 | Starting point |
| `MIN_VISIBLE_TEXT_WORDS` | `fetch.py` | 150 | Shared with the low-confidence detector, deliberately |
| `MIN_LIST_ITEMS` / `MIN_TABLE_ROWS` | `content.py` | 3 / 3 | Part of the original signed-off proposal, not separately flagged until this audit |
| Structured Data's "any block parses" rule | `structured_data.py` | — | More forgiving than this scope's usual harsh-treatment default, by design |
| `Article` permissiveness | `structured_data.py` | not denylisted | Chose false-negative risk over false-positive risk |
| `REQUIRED_PROPERTIES` type coverage | `structured_data.py` | 8 types | Small, hardcoded, growing |
| Technical Quality's internal split | `technical.py` | 5/5/5/5 | Starting point |
| `PSI_SCORE_THRESHOLD` | `technical.py` | 90 | Binary gate, not three-tier |
| `ALT_COVERAGE_THRESHOLD` | `technical.py` | 0.90 | Part of the original signed-off proposal, not separately flagged until this audit |
| `MIN_INTERNAL_LINKS` | `technical.py` | 3 | Unresearched, deliberately low bar |
| Search Evidence's internal split | `search_evidence.py` | 5/5/5 | Starting point |
| `IMPRESSIONS_THRESHOLD` | `search_evidence.py` | 100 | Over the 90-day window |
| `CTR_THRESHOLD` | `search_evidence.py` | 0.02 | |
| `QUERY_DIVERSITY_THRESHOLD` | `search_evidence.py` | 5 | |
| `WINDOW_DAYS` | `gsc_client.py` | 90 | Matches WeekLift's own striking-distance precedent |
| `DATA_LAG_DAYS` | `gsc_client.py` | 3 | **Not explicitly signed off** — introduced during build to account for GSC's processing lag, flagged for review |

See `DECISIONS_LOG.md` for the full reasoning behind each of these,
including the ones that were considered and rejected.

---

## Architecture

```
page-health-score/
├── page_health/
│   ├── fetch.py            # HTTP fetch + BeautifulSoup parsing + confidence model -- all I/O + confidence assessment
│   ├── crawlability.py     # pure scoring, no I/O
│   ├── content.py          # pure scoring, no I/O
│   ├── structured_data.py  # pure scoring, no I/O
│   ├── pagespeed_client.py # all PSI I/O, throttle/retry
│   ├── technical.py        # pure scoring, no I/O
│   ├── gsc_client.py       # the ONLY file touching GSC
│   ├── search_evidence.py  # pure scoring, no I/O
│   ├── score.py            # orchestrates everything, builds the ranked fix list
│   ├── cli.py               # argument parsing / output formatting
│   └── __main__.py          # enables `python -m page_health <url>`
├── tests/unit/               # 153 tests, all external I/O mocked -- no test makes a real network call
├── SCOPE_OF_WORK.md          # authoritative scope, living document (§9 out-of-scope, §10 tunables, §11 integration notes)
├── DECISIONS_LOG.md           # every sign-off decision, with the options considered and why
└── BUILD_ROADMAP.md           # the day-by-day build plan this project followed
```

Every scoring function is pure (no I/O) and takes already-fetched data as
input. All I/O is isolated to `fetch.py`, `pagespeed_client.py`, and
`gsc_client.py` — the last being the only file anywhere in the codebase
that talks to Google Search Console, a structural boundary (not just a
convention) that keeps this repo cleanly separable from WeekLift until
integration (§11) actually happens.

## Explicitly out of scope for v1

No ML or LLM calls of any kind. No GEO/AI-citation scoring or claims
(permanent, see above). No JavaScript execution. No framework
fingerprinting. No integration with WeekLift. No auth, multi-tenancy, or
user accounts for this tool itself (GSC's service-account credential is the
operator's own, not a tool-level auth system). No production deployment,
monitoring, or scheduling.
