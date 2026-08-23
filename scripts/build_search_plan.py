#!/usr/bin/env python3
"""Turn config/search_matrix.json into a concrete list of portal CLI invocations.

Usage:
    python3 build_search_plan.py [--matrix PATH] [--date YYYY-MM-DD] [--portal NAME]

Writes one tab-separated invocation per line to stdout:

    <name>\t<portal>\t<arg>\t<arg>...

`name` is a filesystem-safe slug used for the run's temp file
(/tmp/jobsearch_portal_<name>_<date>.json), so it must be unique per line.
`portal` is the skill directory name under .agents/skills/<portal>-search/.

Why this exists as a separate script rather than a bash loop: the LinkedIn matrix is
13 queries x 10 geos = 130 pairs against a hard cap of 60 requests per run, so the
selection needs rotation arithmetic that is worth testing. Bash 3.x (what macOS
ships) has no associative arrays to express it cleanly.

Rotation contract:
  - Geos listed in `always_include_geos` are emitted on every run (primary market).
  - The remaining pairs rotate through a deterministic window keyed on the date, so
    every pair is reached across a few days and no single run exceeds the cap.
  - `max_requests_per_run` is a hard stop on the run's *total* LinkedIn traffic,
    searches and Phase 1c `detail` enrichment together. `detail_enrich_budget` is
    reserved out of it before rotation, so the search plan is capped at
    `max_requests_per_run - detail_enrich_budget`. Both halves hit the same host,
    and only one number is worth trusting as the exposure limit.
  - If the always-include set alone exceeds the search cap, the set is truncated and
    the drop is reported on stderr rather than silently ignored.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_MATRIX = REPO / "config" / "search_matrix.json"
EPOCH = date(2026, 1, 1)


def slug(text: str) -> str:
    """Filesystem-safe lowercase slug for temp filenames."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return cleaned or "q"


def day_index(day: date) -> int:
    """Days since a fixed epoch, so rotation is deterministic and testable."""
    return (day - EPOCH).days


def load_matrix(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def _linkedin_plan(cfg: dict, index: int, warn) -> list:
    """Build the rotating LinkedIn plan. Returns a list of (name, portal, args)."""
    if not cfg.get("enabled", False):
        return []

    jobage = str(cfg.get("jobage_days", 14))
    limit = str(cfg.get("limit_per_query", 10))
    always_geos = cfg.get("always_include_geos", [])
    geos = cfg.get("geos", [])

    # `max_requests_per_run` bounds the whole run's LinkedIn traffic, not just its
    # searches: Phase 1c spends up to `detail_enrich_budget` more requests on the
    # same host right after this plan executes. Spending the full cap here and then
    # enriching would put the day 25% over the agreed band, so the enrichment share
    # is reserved up front and searches get what remains.
    total_cap = int(cfg.get("max_requests_per_run", 60))
    reserve = max(0, int(cfg.get("detail_enrich_budget", 0)))
    if reserve >= total_cap:
        # Reserving everything would plan zero searches, and enrichment has nothing
        # to enrich without them. Searching is the load-bearing half; say so loudly.
        warn(f"linkedin: detail_enrich_budget ({reserve}) leaves no room under the "
             f"cap of {total_cap}; searching with 1 request and letting enrichment "
             "take the rest. Lower detail_enrich_budget or raise the cap.")
        cap = 1
    else:
        cap = total_cap - reserve

    # (track_id, query) pairs, in declared order, from enabled tracks only
    queries = [
        (track_id, q)
        for track_id, track in cfg.get("tracks", {}).items()
        if track.get("enabled", False)
        for q in track.get("queries", [])
    ]

    def entry(track_id, query, geo):
        name = f"linkedin_{slug(track_id)}_{slug(query)}_{slug(geo)}"
        args = ["search", "-q", query, "-l", geo,
                "--jobage", jobage, "--limit", limit, "--format", "json"]
        return (name, "linkedin", args)

    always = [entry(t, q, g) for g in geos if g in always_geos for t, q in queries]
    rotating = [entry(t, q, g) for g in geos if g not in always_geos for t, q in queries]

    if len(always) > cap:
        warn(f"linkedin: always_include_geos needs {len(always)} requests but the cap is "
             f"{cap} — dropping {len(always) - cap} of them. Raise max_requests_per_run "
             "or shorten always_include_geos.")
        return always[:cap]

    budget = cap - len(always)
    if not rotating or budget <= 0:
        if rotating and budget <= 0:
            warn(f"linkedin: cap {cap} fully consumed by always_include_geos — "
                 f"{len(rotating)} rotating pairs skipped this run.")
        return always

    # Deterministic wrapping window: consecutive days walk the whole list.
    start = (index * budget) % len(rotating)
    take = min(budget, len(rotating))
    window = [rotating[(start + i) % len(rotating)] for i in range(take)]

    if take < len(rotating):
        warn(f"linkedin: {take}/{len(rotating)} rotating pairs this run "
             f"(window starts at {start}); full coverage takes "
             f"{-(-len(rotating) // take)} runs.")

    return always + window


def _simple_plan(portal: str, cfg: dict, warn) -> list:
    """Build the fixed plan for a non-paginated portal."""
    if not cfg.get("enabled", False):
        return []

    jobage = str(cfg.get("jobage_days", 14))
    limit = str(cfg.get("limit_per_query", 20))
    plan = []
    for q in cfg.get("queries", []):
        name = q.get("name") or f"{portal}_{slug(q.get('q', ''))}"
        args = ["search", "-q", q["q"], *q.get("args", []),
                "--jobage", jobage, "--limit", limit, "--format", "json"]
        plan.append((slug(name), portal, args))
    return plan


def build_plan(matrix: dict, index: int, only_portal=None, warn=None) -> list:
    """Return the full ordered plan as a list of (name, portal, args) tuples."""
    if warn is None:
        def warn(msg):
            print(f"Warning: {msg}", file=sys.stderr)

    plan = []
    plan.extend(_linkedin_plan(matrix.get("linkedin", {}), index, warn))
    for portal in ("freehire", "arbeitnow", "weworkremotely"):
        plan.extend(_simple_plan(portal, matrix.get(portal, {}), warn))

    if only_portal:
        plan = [p for p in plan if p[1] == only_portal]

    names = [p[0] for p in plan]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        # Duplicate names would make two queries share one temp file, so the second
        # would silently overwrite the first's results.
        warn(f"duplicate plan names would collide in temp files: {sorted(dupes)}")

    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today. Drives rotation.")
    parser.add_argument("--portal", help="Emit only this portal's invocations.")
    parser.add_argument("--count", action="store_true",
                        help="Print the number of planned requests and exit.")
    args = parser.parse_args()

    if not args.matrix.is_file():
        print(f"Error: matrix not found at {args.matrix}", file=sys.stderr)
        return 1

    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else date.today()
    plan = build_plan(load_matrix(args.matrix), day_index(day), args.portal)

    if args.count:
        print(len(plan))
        return 0

    for name, portal, cli_args in plan:
        print("\t".join([name, portal, *cli_args]))

    linkedin_count = sum(1 for p in plan if p[1] == "linkedin")
    print(f"Planned {len(plan)} requests ({linkedin_count} LinkedIn) for {day}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
