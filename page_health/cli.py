"""CLI entry point: python -m page_health <url>

Per §12, runs end-to-end against an arbitrary public URL with zero WeekLift
dependency -- PSI and GSC credentials are optional; scoring degrades
(components rescale out) rather than failing when they're absent.
"""

import sys

from page_health.fetch import ConfidenceTier
from page_health.score import score_page


def _print_component(name: str, result) -> None:
    if result is None:
        return
    print(f"  {name}: {result.points}/{result.max_points}")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("Usage: python -m page_health <url>", file=sys.stderr)
        return 2

    url = argv[0]
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
