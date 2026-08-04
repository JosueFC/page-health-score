# Page Health Score

Standalone, zero-cost, rules-based 0-100 SEO health scorer for a single web page.

**Status:** Under construction. Crawlability is the only scored component so far.

Full documentation of the scoring model, known limitations, and tunable
values will be written at the end of the build (see BUILD_ROADMAP.md, Day 6),
per SCOPE_OF_WORK.md §12's definition of done. See SCOPE_OF_WORK.md in this
repo for the complete, authoritative scope.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running tests

```bash
pytest
```
