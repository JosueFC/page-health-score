# Page Health Score — Scope of Work

**Status:** v1 scope, pre-build. Standalone project, not yet integrated with WeekLift.
**Repo:** New, separate from `weeklift` (see rationale below).

---

## 1. What this is

A deterministic, rules-based 0–100 score evaluating a single page's technical SEO
and content quality. No ML, no LLM calls, in v1.

## 2. What this explicitly is NOT

**This is not a GEO (Generative Engine Optimization) score, and must never be
marketed or designed as one.** Evidence suggests structured data and Lighthouse
scores don't reliably correlate with LLM citation behavior. This tool stands on
its own as a legitimate SEO hygiene metric — a page can score well here and that
claim should be defensible on its own terms, without any implied claim about
AI-citation likelihood.

This constraint applies to more than the scoring logic — it applies to every
piece of copy, every field name, and every future UI surface this tool produces.
The reserved 10-point buffer (see §3) exists for *possible future* content-clarity
signals that GEO research associates with citation-worthiness (statistics,
sourcing, clear writing) — but it is unbuilt and unscored in v1, precisely so the
tool never implies a capability it doesn't have.

## 3. Scoring model (100 points, five components)

| Component | Points | Signals |
|---|---|---|
| Crawlability | 20 | 200 status, not noindex, self-referencing canonical, page reachable/fetchable |
| Content structure | 20 | Title present, one H1, sensible H2/H3 hierarchy, sufficient body copy, use of lists/tables/FAQ blocks |
| Structured data | 15 | JSON-LD present, valid parsing, type specificity, required properties filled — hygiene check, not a ranking driver |
| Technical quality | 20 | PageSpeed Insights SEO + performance scores, image alt coverage, internal linking — a floor/gate, not a graded quality signal |
| Search evidence | 15 | GSC impressions, CTR, query diversity — proxy for real search traction |
| *(reserved)* | 10 | Buffer for future content-clarity signals (statistics, sourcing, clear writing) — **unbuilt in v1, see §2** |

### Crawlability breakdown (the only component fully specified so far — CONFIRMED)

| Signal | Points | Reasoning |
|---|---|---|
| Final response is HTTP 200 | 10 | Most severe binary failure — anything else means the page effectively doesn't exist to a crawler |
| No `noindex` (meta robots OR `X-Robots-Tag` header — either present fails this) | 6 | An explicit, deliberate site-owner signal, independent of everything else being fine |
| Canonical tag present and self-referencing | 4 | Softer signal — Google treats this as a strong hint, not an absolute directive |

- **Redirects:** followed; score reflects the final destination. Original vs.
  final URL recorded in output as context, not separately points-scored.
- **Malformed canonical** (empty `href`, unresolvable relative URL, multiple
  conflicting canonical tags): scored identically to "absent" (0 of 4 points).
  No partial-credit in-between state — a confusing signal is treated at least
  as harshly as no signal.

**Content Structure, Structured Data, Technical Quality, and Search Evidence
scoring rules are NOT yet specified** — to be proposed and signed off on
individually, same process as Crawlability, in that build order (§6).

## 4. Confidence model

Three tiers, applied to the whole score, not per-component:

1. **Unscored.** No HTTP response at all, non-HTML content-type, or connection
   failure. Nothing downstream can be evaluated honestly — return `None` with an
   explicit reason, never a misleadingly low number.
2. **Scored, low-confidence.** Page fetched and parsed, but one of two specific
   conditions detected (below). Score is still computed from whatever was
   actually extractable — never gated, never guessed-and-corrected.
3. **Scored, high-confidence.** Complete, well-formed HTML, reasonable extracted
   text length, no caveats triggered.

### The two low-confidence detectors — CONFIRMED, with a hard wording constraint

**Governing principle (non-negotiable, applies to all future detectors added to
this model, not just these two):** reason codes report what was *observed*,
never what's *inferred*. Inference belongs only in human-readable prose aimed at
a reader who can apply judgment — never in a machine-checkable reason string,
because reason strings are what future code (and future us) will trust literally.

| Detector | Reason code (exact string) | What it actually checks | What it must NOT claim |
|---|---|---|---|
| Possible incomplete download | `closing_html_tag_not_found` | Raw response text lacks a case-insensitive `</html>` | Must NOT say "truncated" anywhere — a legitimately sloppy-but-complete legacy/hand-rolled page looks identical to this check |
| Possible thin/JS-rendered content | `low_visible_text_word_count` | Extracted visible text below `MIN_VISIBLE_TEXT_WORDS` despite well-formed HTML | Must NOT say "JS-rendered" or "SPA detected" anywhere — a legitimately short page (pricing, contact, single-product landing) looks identical to this check |

- `MIN_VISIBLE_TEXT_WORDS = 150` is a **named, documented, explicitly tunable
  constant** — not a decided value. Comment in code must state it's a starting
  point pending recalibration against real customer pages, matching the
  WeekLift threshold-personalization precedent (documented, revisit-later, not
  permanently settled).
- **Raw word count is always included in the output**, unconditionally — not
  just the boolean flag — so the threshold can be retuned from real evidence
  later rather than guessed at twice.
- Unit test requirement: assert the `closing_html_tag_not_found` reason string
  never contains the substring `"truncat"`, anywhere — a permanent guard
  against the wording drifting back toward false certainty in a future edit.
- **Output schema requirement:** confidence tier and numeric score are
  structurally separate fields from the start, never merged into one combined
  field/badge. A future UI must not be able to accidentally render "low
  confidence" and "bad score" as the same visual signal — this is a schema-level
  decision made now specifically so it can't be gotten wrong later by a UI
  implementation that wasn't thinking about this distinction.

## 5. Known, accepted limitations (v1)

Documented explicitly rather than silently producing misleading output:

- **Cannot execute JavaScript.** `requests` + BeautifulSoup only. Client-side-
  rendered content is invisible to this tool — mitigated (not solved) by the
  low-visible-text-word-count detector above, honestly labeled as a possibility,
  not a diagnosis.
- **No framework fingerprinting.** Deliberately not attempting to detect *why*
  a page might be JS-rendered (looking for `id="root"`, `id="__next"`, script-
  tag ratios, etc.). That's a fragile, ongoing arms race against every
  framework's conventions; the blunter word-count signal degrades more
  gracefully (worst case: over/under-cautious, never confidently wrong about
  cause).
- **PageSpeed Insights rate limits are real**, even though the dollar cost is
  zero — roughly 1 req/sec informally enforced unauthenticated, plus a daily
  quota with an API key. Must be designed around from the start of the
  Technical Quality component, not discovered under load.
- **Truncation vs. malformed-but-complete HTML cannot be distinguished** by the
  closing-tag check alone (see §4). Accepted as a v1 simplification; the
  wording constraint above is the mitigation, not a fix.

## 6. Architecture

**Separate, standalone repository** — not a folder inside `weeklift`. Rationale
(confirmed): the two projects don't share a deployment lifecycle right now (one
is live production, the other is pre-integration/local-only); a separate repo
keeps `weeklift`'s `requirements.txt`/`render.yaml`/`ci.yml` untouched by
dependencies nothing in production uses yet, enforces the
Search-Evidence-is-the-only-GSC-touching-component boundary *structurally*
rather than by convention, and avoids adding a large parallel effort's commit
noise to a codebase that just went through a real production incident.
Integration later is a deliberate, one-time, well-understood event — folding a
finished standalone package in as a new sibling module — not something worth
paying continuous coupling risk for now in exchange for a slightly easier merge
later.

```
page-health-score/
├── page_health/
│   ├── __init__.py
│   ├── fetch.py            # HTTP fetch + BeautifulSoup parsing -- all I/O lives here
│   ├── crawlability.py     # pure scoring function, no I/O
│   ├── content.py          # (later)
│   ├── structured_data.py  # (later)
│   ├── technical.py        # (later)
│   ├── search_evidence.py  # (later -- ONLY component touching GSC)
│   ├── score.py            # combines sub-scores, builds confidence note + ranked fix list
│   └── cli.py               # python -m page_health <url>
├── tests/
│   └── unit/
├── requirements.txt
├── pyproject.toml
└── README.md
```

Design philosophy, deliberately mirroring `weeklift/app/services/flagging.py`'s
style: pure, testable rule functions with no I/O; I/O isolated entirely to
`fetch.py`; docstrings that explain *why* a rule/threshold/limitation exists the
way it does, not just what it does; deterministic, no hidden state.

## 7. Build order (deliberate, not arbitrary)

1. **Crawlability** (fully specified, §3) — smallest, no fetch/confidence
   machinery dependency beyond the basic HTTP call itself.
2. **Content structure**
3. **Structured data**
4. **Technical quality** — first component needing an external API (PageSpeed
   Insights) beyond the page fetch itself.
5. **Search evidence** — deliberately LAST. The only component requiring GSC
   data at all; 85 of 100 points (everything above) needs nothing but a URL.
   Building in this order means the tool is usable, testable, and iterable from
   day one against any public URL on the internet, with zero dependency on
   WeekLift's auth/OAuth/Supabase infrastructure until the very end.

Each component gets its own scoring-rule proposal and explicit sign-off before
implementation, same process as Crawlability and as every WeekLift roadmap
session in this project.

## 8. Output format (per page)

- Overall score (0–100) + 5 sub-scores, OR `None` + reason if unscored (§4).
- Confidence tier + specific reason code(s), structurally separate from the
  score (§4).
- Raw diagnostic values backing any low-confidence flag (e.g. actual word
  count) — always present, not just the boolean.
- 3–5 highest-impact fixes, ranked by point upside.
- Redirect info (original URL vs. final URL), when applicable.

## 9. Explicitly out of scope for v1

- No ML, no LLM calls of any kind.
- No GEO/AI-citation scoring or claims (§2 — permanent constraint, not just a
  v1 simplification).
- No JavaScript execution/rendering (headless browser, Playwright, etc.).
- No framework fingerprinting.
- No integration with the WeekLift weekly digest batch/pipeline.
- No auth, no multi-tenancy, no user accounts — CLI/local use only.
- No production deployment, monitoring, or scheduling of any kind.

## 10. Tunable / not-yet-final values (revisit-later list)

Tracked here so nothing gets silently treated as permanent:

- `MIN_VISIBLE_TEXT_WORDS = 150` (§4) — needs recalibration against real
  customer pages once available.
- All five components' point weights (§3) — Crawlability's internal 10/6/4
  split is confirmed; the 20/20/15/20/15/10 split across components has not
  been separately stress-tested and may warrant revisiting once more
  components are built out.
- **`X-Robots-Tag` user-agent scoping (§3, noindex signal)** — v1 treats any
  `noindex` token in the header as a fail, regardless of whether it's scoped
  to a specific user-agent (e.g. `googlebot: noindex`) or applies globally.
  Conservative default, chosen for simplicity and consistency with the
  "deliberate site-owner signal" reasoning in §3. May warrant per-user-agent
  parsing later if real pages show this to be too blunt.
- **Content Structure's internal 4/4/4/4/4 split** (§3, Day 2) — title / H1 /
  heading hierarchy / body copy / lists-tables-FAQ each weighted equally as
  a starting point, same status as Crawlability's 10/6/4 split had before
  being confirmed: not yet stress-tested against real pages, may warrant
  reweighting.
- **Body copy sufficiency reuses `MIN_VISIBLE_TEXT_WORDS`** (§3/§4, Day 2) —
  Content Structure's body-copy-sufficiency signal intentionally shares the
  same constant as the `low_visible_text_word_count` confidence detector,
  rather than using a separate scoring threshold. Accepted as intentional
  redundancy for v1 (a thin page fails both checks, seen from two angles) —
  revisit if real-world use shows the two "thin" concepts (distrust-the-fetch
  vs. bad-content) need to diverge.

## 11. Integration trigger

Deferred until WeekLift has paying users (per existing plan). Integration
itself — scope, whether it becomes part of the weekly digest vs. a separate
on-demand dashboard action, how Search Evidence reuses `gsc_client.py`'s
page-analytics shape — is explicitly **not scoped here** and will get its own
Scope of Work when that milestone arrives, not decided speculatively now.

## 12. Definition of done for v1

- All five components scored with confirmed, documented rules and passing unit
  tests (pure functions, no I/O, mirroring `flagging.py`'s test rigor).
- `fetch.py`'s three-tier confidence model implemented per §4, including both
  detectors and their wording constraints.
- CLI runs end-to-end against an arbitrary public URL, no WeekLift dependency
  required until Search Evidence is invoked.
- README documents the scoring model, all known limitations (§5), and all
  tunable values (§10) in one place a future reader (including future us) can
  find without re-deriving this document.
