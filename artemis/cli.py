"""Command line: `python -m artemis <command>`."""

from __future__ import annotations

import argparse
import json
import sys

from .core.agent import DEFAULT_MAX_TOKENS, DEFAULT_MODEL
from .core.models import ALL_EFFORT
from .core.registry import Registry
from .connectors.eahw import EAHWConnector
from .connectors.eahw.places import KNOWN_PLACES

#: Systems available to the CLI. Add a connector here once it works.
def build_registry() -> Registry:
    return Registry([EAHWConnector()])


def cmd_systems(args: argparse.Namespace) -> int:
    reg = build_registry()
    print(reg.systems_overview())
    print(f"\n{len(reg.tools())} tool(s):")
    for t in reg.tools():
        print(f"  {t['name']}")
    return 0


def cmd_datasets(args: argparse.Namespace) -> int:
    reg = build_registry()
    out = json.loads(reg.dispatch(
        f"{args.system}__list_datasets",
        {"filter": args.filter or "", "only_readable": args.readable},
    ))
    if "error" in out:
        print(out["error"], file=sys.stderr)
        return 1
    print(f"{out['count']} dataset(s)\n")
    for d in out["datasets"]:
        flag = "R" if d.get("readable") else "-"
        units = f" [{d['units']}]" if d.get("units") else ""
        print(f"[{flag}] {d['name']}{units}")
        print(f"      id={d['id']}  latest={d.get('latest', '-')}"
              f"  period={d.get('temporal', '-')}  mode={d.get('mode', '-')}"
              f"  res={d.get('resolution', '-')}")
        if not d.get("readable") and d.get("reason"):
            print(f"      unreadable: {d['reason']}")
    print("\n[R] = readable")
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    reg = build_registry()
    call: dict[str, object] = {"layer": args.dataset}
    if args.place:
        call["place"] = args.place
    else:
        call["lon"], call["lat"] = args.lon, args.lat
    if args.date:
        call["date"] = args.date
    out = json.loads(reg.dispatch(f"{args.system}__query_point", call))
    if "error" in out:
        print(out["error"], file=sys.stderr)
        return 1
    if out.get("no_data"):
        print(f"No data: {out['dataset_name']} at "
              f"{out['query']['lon']},{out['query']['lat']} on {out['query']['time']}")
        print(f"  {out['note']}")
        return 0
    for r in out["readings"]:
        unit = f" {r['units']}" if r.get("units") else ""
        print(f"{out['dataset_name']}: {r['variable']} = {r['value']:g}{unit}")
        p, at = r["provenance"], r["at"]
        print(f"  system={p['system']} dataset={r['dataset_id']} "
              f"lon={at['lon']} lat={at['lat']} time={at['time']}")
        print(f"  source={p['source']}")
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    reg = build_registry()
    report = reg.health()
    exit_code = 0
    for system, h in report.items():
        print(f"=== {system} ===")
        if not h.get("implemented"):
            print(f"  no health probe: {h.get('note', 'not implemented')}")
            continue
        c = h["counts"]
        print(f"  {c['answered']} answered, {c['empty']} empty, {c['broken']} broken")
        for row in h["answered"]:
            unit = f" {row['units']}" if row.get("units") else ""
            print(f"    OK      {row['layer']:52s} {row['variable']}="
                  f"{row['value']:.4g}{unit} @{row['date']}")
        for row in h["empty"]:
            print(f"    empty   {row['layer']:52s} @{row['date']}")
        for row in h["broken"]:
            print(f"    BROKEN  {row['layer']:52s} {row['error'][:60]}")
        if not h["answered"]:
            exit_code = 1
    return exit_code


def cmd_ask(args: argparse.Namespace) -> int:
    from .core.agent import ask  # late import: needs ANTHROPIC_API_KEY
    from .core.models import UnsupportedSetting

    try:
        answer, trace = ask(
            args.question,
            build_registry(),
            model=args.model,
            effort=args.effort,
            max_tokens=args.max_tokens,
            verbose=args.verbose,
        )
    except UnsupportedSetting as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(answer)
    if args.trace:
        print(f"\n--- {trace.summary()} ---")
        for note in trace.notes:
            print(f"note: {note}")
        print(trace.render())
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    from .core.agent import DEFAULT_EFFORT, DEFAULT_MODEL
    from .core.models import PROFILES

    print(f"default: {DEFAULT_MODEL} at effort {DEFAULT_EFFORT}\n")
    for name, profile in PROFILES.items():
        effort = ", ".join(profile.effort_levels) or "not supported"
        mark = " (default)" if name == DEFAULT_MODEL else ""
        print(f"  {name}{mark}")
        print(f"      thinking={profile.thinking}  effort={effort}")
    print("\nAn unlisted model ID is accepted and assumed to be newer than this")
    print("table; --trace will say so.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="artemis",
        description="Point an agent at a system; read, analyse and explain it.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("systems", help="list connected systems and their tools")
    s.set_defaults(func=cmd_systems)

    d = sub.add_parser("datasets", help="list what a system holds")
    d.add_argument("--system", default="eahw")
    d.add_argument("--filter", help="name substring")
    d.add_argument("--readable", action="store_true", help="only readable datasets")
    d.set_defaults(func=cmd_datasets)

    q = sub.add_parser("query", help="read one dataset at one point")
    q.add_argument("dataset", help="dataset id or name substring")
    q.add_argument("--system", default="eahw")
    q.add_argument("--place", help=f"one of: {', '.join(sorted(KNOWN_PLACES))}")
    q.add_argument("--lon", type=float)
    q.add_argument("--lat", type=float)
    q.add_argument("--date", help="YYYY-MM-DD (default: dataset's latest)")
    q.set_defaults(func=cmd_query)

    h = sub.add_parser("health", help="probe what each system actually serves")
    h.set_defaults(func=cmd_health)

    a = sub.add_parser("ask", help="ask the agent (needs ANTHROPIC_API_KEY)")
    a.add_argument("question")
    a.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Claude model ID (default: {DEFAULT_MODEL}). "
             "See `artemis models` for what is known.",
    )
    a.add_argument(
        "--effort", choices=ALL_EFFORT, default=None,
        help="thinking depth; defaults to 'high' on models that support it, "
             "and must be omitted for models that do not (e.g. Haiku).",
    )
    a.add_argument(
        "--max-tokens", type=int, default=DEFAULT_MAX_TOKENS, dest="max_tokens",
        help=f"cap on thinking plus response text (default: {DEFAULT_MAX_TOKENS})",
    )
    a.add_argument("--trace", action="store_true", help="print the call trace")
    a.add_argument("--verbose", action="store_true", help="stream tool calls")
    a.set_defaults(func=cmd_ask)

    m = sub.add_parser("models", help="list known models and their request shapes")
    m.set_defaults(func=cmd_models)

    args = p.parse_args(argv)
    if args.cmd == "query" and not args.place and (args.lon is None or args.lat is None):
        p.error("query needs --place, or both --lon and --lat")
    return args.func(args)
