# Page Health Score — Build Roadmap

Sequence matches SCOPE_OF_WORK.md §7. Each stop follows the same pattern:
propose exact scoring rules for that component → get explicit sign-off →
implement → test → commit/PR. **This roadmap does not contain any component's
scoring rules.** Those get proposed fresh at the start of each day, same as
Crawlability.

Paste the prompt for a day verbatim to kick that day off. Don't start a new
day until the previous day's "Done when" checklist is fully checked.

---

## Day 1 — Repo scaffold + Crawlability

**Prompt to paste:**
> Day 1: scaffold the repo per §6, then implement Crawlability (§3) end to
> end — fetch.py's basic HTTP handling (just enough for Crawlability, per our
> agreement that the full confidence model waits for Content Structure),
> crawlability.py's pure scoring function, and tests covering the
> 200-status/noindex/canonical-present signals, the malformed-canonical and
> non-self-referencing-canonical edge cases (distinguishable reasons, same
> 0/4 outcome), and the noindex X-Robots-Tag any-token-fails default.

**Done when:**
- [ ] Repo scaffold exists matching §6's tree (`page_health/`, `tests/unit/`, `requirements.txt`, `pyproject.toml`, `README.md` stub)
- [ ] `fetch.py` implements: follow redirects, return `FetchResult` (final_url, original_url, status_code, headers, html, error) — no confidence-tier logic yet
- [ ] `crawlability.py` implements `score_crawlability()` as a pure function, no I/O, assumes `fetch_result.html is not None`
- [ ] Function returns points (0–20), per-signal breakdown, and raw observed values (status_code, noindex_source, canonical_value/reason) — not just booleans
- [ ] Reachability/connection-failure handling lives in fetch.py/score.py, not in crawlability.py (per flag #1 resolution)
- [ ] Tests pass covering: 200/non-200 status, noindex via meta robots, noindex via X-Robots-Tag (any token, any user-agent scope), self-referencing canonical present, canonical absent, canonical malformed (empty href / unresolvable relative / multiple conflicting tags), canonical resolvable-but-points-elsewhere — and that the last two produce distinguishable reason strings at the same 0/4 point value
- [ ] `git init` done locally; you've created the empty GitHub repo and done the first push (walked through step by step)
- [ ] Real commit message(s) describing what was built, not placeholder text

---

## Day 2 — Content Structure + full three-tier confidence model

Content Structure is the first component needing text extraction depth
(word count, heading hierarchy), so this is also where §4's full confidence
model (both detectors) gets built, per the SoW's stated dependency.

**Prompt to paste:**
> Day 2: propose Content Structure's exact scoring rules (title, H1, H2/H3
> hierarchy, body copy sufficiency, lists/tables/FAQ blocks — 20 points) for
> sign-off. Once confirmed, extend fetch.py with the full three-tier
> confidence model per §4 — both detectors (`closing_html_tag_not_found`,
> `low_visible_text_word_count`), `MIN_VISIBLE_TEXT_WORDS = 150` as a named
> tunable constant, raw word count always in output — then implement
> content.py's pure scoring function and tests.

**Done when:**
- [ ] Content Structure point breakdown proposed and explicitly signed off before any code
- [ ] `fetch.py` extended with confidence tiers: Unscored / Scored-low-confidence / Scored-high-confidence, per §4
- [ ] Both detectors implemented with the exact locked-in reason-code strings
- [ ] Unit test asserts `closing_html_tag_not_found`'s reason string never contains substring `"truncat"`, anywhere
- [ ] Unit test asserts `low_visible_text_word_count` reason string never contains `"JS"`, `"SPA"`, or `"rendered"`
- [ ] `MIN_VISIBLE_TEXT_WORDS` is a named constant with a docstring/comment stating it's a starting point pending recalibration (not a bare literal)
- [ ] Raw word count included in output unconditionally, not just the boolean flag
- [ ] Confidence tier and numeric score are structurally separate output fields (schema-level, not just convention)
- [ ] `content.py` implements pure scoring function, no I/O
- [ ] Tests pass covering: title present/absent, exactly-one-H1/zero-H1/multiple-H1, sensible/nonsensical heading hierarchy, body copy above/below threshold, presence/absence of lists/tables/FAQ blocks, both confidence detectors triggering independently and together
- [ ] Branch confirmed before editing; tests green before commit; PR opened against the pushed repo

---

## Day 3 — Structured Data

**Prompt to paste:**
> Day 3: propose Structured Data's exact scoring rules (JSON-LD present,
> valid parsing, type specificity, required properties filled — 15 points,
> hygiene check not ranking driver) for sign-off. Once confirmed, implement
> structured_data.py's pure scoring function and tests.

**Done when:**
- [ ] Structured Data point breakdown proposed and explicitly signed off before any code
- [ ] `structured_data.py` implements pure scoring function, no I/O
- [ ] Handles: no JSON-LD present, malformed/unparseable JSON-LD, valid JSON-LD with generic vs. specific `@type`, missing vs. present required properties for the detected type
- [ ] Raw diagnostic values in output (e.g. detected type, which required properties were missing) — not just a boolean/score
- [ ] Tests pass covering each case above, including multiple JSON-LD blocks on one page (if in scope — confirm during rule proposal)
- [ ] Branch confirmed before editing; tests green before commit; PR opened

---

## Day 4 — Technical Quality (first external-API component)

**Prompt to paste:**
> Day 4: propose Technical Quality's exact scoring rules (PageSpeed Insights
> SEO + performance scores, image alt coverage, internal linking — 20
> points, floor/gate not graded quality signal) for sign-off, including how
> PSI rate limits (§5: ~1 req/sec unauthenticated, daily quota with API key)
> are handled architecturally. Once confirmed, implement technical.py and
> the PSI client, and tests (with PSI calls mocked).

**Done when:**
- [ ] Technical Quality point breakdown proposed and explicitly signed off, including the floor/gate framing (not a graded curve)
- [ ] Rate-limit handling strategy proposed and confirmed before implementation (e.g. backoff, caching, key vs. no-key path) — not discovered under load
- [ ] PSI client isolated from scoring logic (I/O separate from pure function, same pattern as fetch.py/crawlability.py)
- [ ] `technical.py` implements pure scoring function, no I/O, takes already-fetched PSI data + parsed HTML as input
- [ ] Image alt coverage and internal linking logic implemented and testable without network calls
- [ ] Tests pass with PSI responses mocked — covering high/low PSI scores, PSI request failure/timeout, full/partial/zero alt coverage, strong/weak internal linking
- [ ] No test makes a real PSI network call
- [ ] Branch confirmed before editing; tests green before commit; PR opened

---

## Day 5 — Search Evidence (last, GSC-dependent)

**Prompt to paste:**
> Day 5: propose Search Evidence's exact scoring rules (GSC impressions,
> CTR, query diversity — 15 points, proxy for real search traction) for
> sign-off, including how this component alone touches GSC data,
> structurally enforced per §6's rationale. Once confirmed, implement
> search_evidence.py and tests (with GSC calls mocked).

**Done when:**
- [ ] Search Evidence point breakdown proposed and explicitly signed off
- [ ] GSC client isolated to this component only — no other file imports or calls it, preserving §6's structural boundary
- [ ] `search_evidence.py` implements pure scoring function, no I/O, takes already-fetched GSC data as input
- [ ] Tests pass with GSC responses mocked — covering no-data/insufficient-history case (should this be Unscored-adjacent or its own reason? confirm during rule proposal), strong/weak impressions, high/low CTR, high/low query diversity
- [ ] No test makes a real GSC network call
- [ ] `score.py` now combines all 5 sub-scores + confidence note + ranked fix list per §8, with score.py as the single integration point
- [ ] CLI (`cli.py`) runs end-to-end against an arbitrary public URL with zero WeekLift dependency until Search Evidence is invoked, per §12
- [ ] Branch confirmed before editing; tests green before commit; PR opened

---

## Day 6 — Wrap-up / Definition of Done (§12)

**Prompt to paste:**
> Day 6: run through §12's definition of done as a checklist against the
> actual repo state. Write the README covering the scoring model, all §5
> limitations, and all §10 tunable values in one place. Flag anything in §9
> (out of scope) that crept back in, and anything in §10 that's still
> undocumented-but-decided.

**Done when:**
- [ ] All five components scored with confirmed rules and passing unit tests, pure/no-I/O, verified against §12 line by line
- [ ] `fetch.py`'s three-tier confidence model fully implemented, both detectors' wording constraints tested
- [ ] CLI runs end-to-end against an arbitrary public URL
- [ ] README exists documenting: scoring model (all 5 components + reserved buffer), all §5 limitations, all §10 tunable values (including the noindex X-Robots-Tag entry), in one place
- [ ] §9 out-of-scope list re-checked against actual repo — nothing crept back in unflagged
- [ ] §10 re-checked — nothing decided-but-undocumented remains
