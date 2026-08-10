"""Enables `python -m page_health <url>` per SCOPE_OF_WORK.md §6/§12.

cli.py's own `if __name__ == "__main__":` guard only fires when cli.py is
invoked directly (`python page_health/cli.py <url>` or
`python -m page_health.cli <url>`) -- invoking the package itself with
`-m page_health` requires this file. Caught during the Day 6 §12
verification pass by actually running the command, not just reading the
code.
"""

import sys

from page_health.cli import main

if __name__ == "__main__":
    sys.exit(main())
