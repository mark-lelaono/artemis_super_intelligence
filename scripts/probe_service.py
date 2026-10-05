#!/usr/bin/env python3
"""Health-probe every connected system and print what actually answers.

Run before trusting an analysis, and on a schedule if anything depends on this.
External systems drift: a dataset that answered last week can be empty today,
and a dataset can stay listed while its backing query path breaks.

    python scripts/probe_service.py
    python scripts/probe_service.py --place turkana

Exits non-zero when a system with a health probe returns nothing at all, so it
can gate a cron job or a CI check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from artemis.cli import build_registry  # noqa: E402
from artemis.connectors.eahw.places import KNOWN_PLACES  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--place", default="nairobi",
        help=f"probe point for geospatial systems. One of: "
             f"{', '.join(sorted(KNOWN_PLACES))}",
    )
    args = ap.parse_args()

    registry = build_registry()
    failed = False

    for system, conn in registry.connectors.items():
        print(f"=== {system} ===")
        try:
            if system == "eahw":
                lon, lat = KNOWN_PLACES[args.place]
                health = conn.health(lon=lon, lat=lat)
            else:
                health = conn.health()
        except Exception as exc:
            print(f"  probe failed: {type(exc).__name__}: {exc}")
            failed = True
            continue

        if not health.get("implemented"):
            print(f"  no health probe: {health.get('note', 'not implemented')}")
            continue

        counts = health["counts"]
        print(f"  probe point: {health['probe_point']}")
        print(f"  {counts['answered']} answered, {counts['empty']} empty, "
              f"{counts['broken']} broken\n")
        for row in health["answered"]:
            unit = f" {row['units']}" if row.get("units") else ""
            print(f"    OK       {row['layer']:52s} {row['variable']}="
                  f"{row['value']:.4g}{unit} @{row['date']}")
        for row in health["empty"]:
            print(f"    empty    {row['layer']:52s} @{row['date']}")
        for row in health["broken"]:
            print(f"    BROKEN   {row['layer']:52s} {row['error'][:58]}")
        if not health["answered"]:
            failed = True
        print()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
