# Page Health Score — Decisions Log

Every decision below was proposed with options and trade-offs, then explicitly
signed off before any code was written, per the review-before-building
workflow this project uses throughout. This document exists so a decision's
reasoning can be found in one place without re-deriving it from commit
history or re-reading the SoW cover-to-cover. Tunable ones are also tracked
in `SCOPE_OF_WORK.md` §10 — this log is the fuller "why," §10 is the
scannable list.

Each entry notes whether it's **Confirmed** (settled, not expected to
change) or **Tunable** (documented default, open to revisiting against real
data — tracked in §10).

---

## Scope review (pre-Day-1)

### 1. "Reachable/fetchable" is not a fourth Crawlability signal
**Confirmed.** §3's prose row listed "page reachable/fetchable" alongside the
three scored Crawlability signals, but the confirmed point table only
allocates 20 points across three (10/6/4). This was leftover text from
before §4's confidence model existed — reachability isn't a fourth point
bucket on top of that split. Resolution: `crawlability.py`'s pure function
assumes it's only ever called on a page that already produced an HTTP
response; reachability/connection-failure handling is gated upstream in
`fetch.py`/`score.py`, not scored inside Crawlability.

### 2. Self-referencing canonical — "points elsewhere" scores same as malformed, but stays distinguishable
**Confirmed.** "Self-referencing canonical" is the actual bar in §3, not just
"canonical present." A canonical that resolves fine but points at a
different page fails the same way a malformed one does (same 0/4 outcome).
But the two are kept as distinguishable reason strings in raw diagnostic
output — "canonical points elsewhere" and "canonical missing/malformed" are
different fixes for a reader, even at an identical score.

### 3. `X-Robots-Tag` noindex — any token fails, regardless of user-agent scoping
**Tunable** (§10). `X-Robots-Tag` can be scoped to a specific user-agent
(e.g. `googlebot: noindex`) or apply globally. v1 default: any `noindex`
token in the header fails the signal regardless of scoping — simplest,
matches §3's "deliberate site-owner signal" reasoning. May warrant
per-user-agent parsing later if real pages show this to be too blunt.

---

## Day 2 — Content Structure

### 4. Point split: 4/4/4/4/4 across title, H1, heading hierarchy, body copy, lists/tables/FAQ
**Tunable** (§10). Proposed as the starting point in the absence of a
principled reason to weight the five sub-signals differently — same status
Crawlability's 10/6/4 split had before being stress-tested. Not yet tested
against real pages; may warrant reweighting once more components are built
out and real scores can be compared.

### 5. Heading hierarchy: 2+2 internal sub-split, not binary
**Confirmed** (mechanism), **Tunable** (whether 2+2 vs. binary is the right
shape — §10, folded into the point-split entry above). Two points for having
any `<h2>` at all; two more for not skipping levels (no `<h3>` before any
`<h2>` in document order, no `<h4>`+ used at all in v1). Implementation
detail resolved during build: "no skipped levels" requires `has_h2` as a
precondition — a page with zero `<h2>` tags can't vacuously pass the
skipped-levels check just because there's nothing to skip. (Caught by a
failing test during Day 2 build, fixed before commit.)

### 6. Body copy sufficiency shares `MIN_VISIBLE_TEXT_WORDS` with the confidence detector
**Tunable** (§10). Two options were on the table: (A) one shared threshold
for both the content-structure score and the `low_visible_text_word_count`
confidence flag, or (B) two separate thresholds — a lower one for "distrust
this fetch" and a higher one for "this is thin content." Chose (A): fewer
tunables to guess at, and the redundancy (a thin page fails both checks) is
treated as honest rather than confusing. Revisit if real use shows the two
concepts need to diverge.

---

## Day 3 — Structured Data

### 7. Staged/dependent signal gating, not independent scoring
**Confirmed.** Each of the four signals (presence → parsing → type
specificity → required properties) gates the next. A page with no JSON-LD
scores 0/15 outright, not partial credit for signals it never had a chance
to fail. Matches the "hygiene check, not a ranking driver" framing in §3
better than independent scoring would, and is consistent with the
harsh-treatment-of-ambiguity precedent set by Crawlability's canonical
handling and Content Structure's H1 handling.

### 8. Parsing: "any block parses" scores full points, not "all must parse" — with unconditional broken-block diagnostics
**Tunable** (§10). Considered "all blocks must parse" (consistent with the
harsh-treatment precedent, but risks zeroing out a well-maintained page over
one stale broken block from a third-party plugin) vs. "any block parses"
(more forgiving of real-world multi-source schema accumulation, but a weaker
signal). Chose "any parses" — but as a condition of that leniency, broken-
block count and per-block parse errors are captured in raw diagnostic output
*unconditionally*, even on a page that scores full points here, so a future
ranked-fix-list (§8, Day 5) can still surface "you have N broken JSON-LD
blocks" as its own separately-ranked fix item regardless of score.

### 9. `Article` treated as a specific type (permissive), not denylisted
**Tunable** (§10). `Article` is common and often legitimate but is also the
generic fallback when someone skips picking `BlogPosting`/`NewsArticle`/etc.
Chose permissive: schema.org doesn't deprecate plain `Article`, and a false
negative (dinging a legitimately-typed page) was judged worse than a false
positive for a hygiene check. Revisit if this proves too permissive once
real pages are scored.

### 10. Unknown `@type` uses a rescale-ceiling, not 0-by-default or full-points-by-default
**Tunable** (§10, with a downstream implication for Day 5). Three options
considered for a specific-but-unrecognized type (e.g. `Recipe`, not in the
hardcoded `REQUIRED_PROPERTIES` map): score 0 (penalizes a page for the
tool's own coverage gap, not anything wrong with the page), score 4 by
default (silently credits something never actually verified — conflicts
with §4's "never guess-and-correct" principle), or exclude the signal from
the denominator entirely (most honest, but breaks score comparability across
pages and adds complexity). Chose the rescale-ceiling: that page's
Structured Data `max_points` drops from 15 to 11, and
`required_properties_status` reports `unchecked_unknown_type` so it's never
confused with a real pass or fail. **Downstream note:** `score.py` (Day 5)
must read `max_points` per-page for this component rather than assuming a
constant 15 when combining sub-scores.

---

## Still open / explicitly deferred

Nothing currently open — all decisions proposed through Day 3 have been
signed off. Day 4 (Technical Quality) and Day 5 (Search Evidence) will add
entries here as their scoring rules are proposed and confirmed.
