"""CLI entry point: python -m page_health <url>

Per §12, runs end-to-end against an arbitrary public URL with zero WeekLift
dependency -- PSI and GSC credentials are optional; scoring degrades
(components rescale out) rather than failing when they're absent.

URL scheme normalization (CLI-only convenience, not built into score_page()
itself): a bare domain like "weeklift.com" is auto-prefixed with "https://",
since most people typing a URL by hand won't include the scheme. This is
deliberately CLI-level, not core-library behavior -- score_page() and
fetch_page() take exactly what they're given and report back precisely what
happened (see fetch.py's invalid_url vs. no_http_response distinction); a
future caller other than this CLI (a future web UI, a batch script) should
not have its input silently rewritten without an explicit choice to do so.
The normalization is never silent here either -- a notice is printed so the
person can see their input was changed, not just guess from the output.
"""

import sys
from urllib.parse import urlsplit

from page_health.fetch import ConfidenceTier
from page_health.score import score_page


def _normalize_url(url: str) -> tuple:
    """Prefix a scheme-less URL with https://. Returns (url, was_changed)."""
    if not urlsplit(url).scheme:
        return f"https://{url}", True
    return url, False


def _print_component(name: str, result) -> None:
    if result is None:
        return
    print(f"  {name}: {result.points}/{result.max_points}")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python -m page_health <url>", file=sys.stderr)
        return 2

    url, was_normalized = _normalize_url(argv[0])
    if was_normalized:
        print(f"No scheme provided -- using {url}", file=sys.stderr)

    result = score_page(url)

    print(f"URL: {result.url}")
    if result.final_url and result.final_url != result.url:
        print(f"Final URL (after redirects): {result.final_url}")

    if result.confidence_tier == ConfidenceTier.UNSCORED:
        print(f"Score: unscored ({result.unscored_reason})")
        return 0

    print(f"Score: {result.score}/100")
    print(f"Confidence: {result.confidence_tier.value}", end="")
    if result.confidence_reason_codes:
        print(f" ({', '.join(result.confidence_reason_codes)})")
    else:
        print()

    print("Components:")
    _print_component("Crawlability", result.crawlability)
    _print_component("Content Structure", result.content_structure)
    _print_component("Structured Data", result.structured_data)
    _print_component("Technical Quality", result.technical_quality)
    _print_component("Search Evidence", result.search_evidence)

    if result.fixes:
        print("\nTop fixes:")
        for fix in result.fixes[:5]:
            upside = f"+{fix.points_upside}pt" if fix.points_upside else "note"
            print(f"  [{upside}] {fix.component}: {fix.description}")

    return 0


if __name__ == "__main__":
    sys.exit(main())