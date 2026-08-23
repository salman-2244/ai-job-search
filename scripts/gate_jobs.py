#!/usr/bin/env python3
"""Enforce the document-generation gate deterministically, after ranking.

Usage:
    python3 gate_jobs.py --jobs /tmp/jobsearch_top5_2026-08-18.json \
        --not-drafted /tmp/jobsearch_not_drafted_2026-08-18.json

The gate decides whether a real CV and cover letter get written for a job:

    score >= 75                                   -> draft
    score >= 60 AND key in alert_matched.json     -> draft
    anything else                                 -> report only

`prompts/pipeline_phase1_rank.md` (Step 5) already states that rule, but a prompt is
an instruction, not an enforcement. If the ranker miscounts, or reports
`alert_matched: true` for a key that is not in the store, documents get drafted for a
job that never cleared the gate — and nothing in the pipeline would notice. So the
gate is re-applied here in code, and **`alert_matched.json` is the authority**: this
script overwrites each job's `alert_matched` and `gate_reason` from the store rather
than trusting what the ranker claimed.

Alert-match changes the *gate*, never the score. Nothing here adds points.

The 30-day expiry lives here too. An alert from three months ago should stop
privileging its posting, or the 60 gate quietly becomes permanent for every job
LinkedIn ever emailed about. Expired, undated and unparseable-date entries all
**fail closed** — the job then needs 75 like any other. Failing open would widen the
gate on the strength of a record we cannot date.

This script never writes to `alert_matched.json` unless asked with `--prune`. The
08:00 run is unattended, and silently rewriting a personal-data store is not
something an unattended job should do; ignoring expired entries achieves the same
gate outcome without touching the file.

Every job pulled by the gate or by the cap is appended to the not-drafted list, so
the report's "Matched but Not Drafted" section shows it and Salman can still draft it
by hand with `/apply`. A gate that silently discarded near-misses would look
identical to a thin day.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ALERTS = REPO / "job_scraper" / "alert_matched.json"
DEFAULT_CONFIG = REPO / "config" / "automation.json"

STRONG_SCORE = 75
MIN_SCORE = 60
EXPIRY_DAYS = 30
DEFAULT_CAP = 5

# Carried onto the not-drafted list. The report table reads score/verdict/track/
# title/company/location/url; `key` keeps the entry dedupable against the ranker's
# own list, and `gate_note` records why the pipeline stopped short of drafting.
CARRY_FIELDS = ("key", "title", "company", "url", "location", "portal", "track",
                "score", "verdict")


def extract_array(raw: str):
    """The JSON array out of the ranker's stdout, or None if there isn't one.

    The ranker is told to emit bare JSON, but a model can still wrap it in a code
    fence or add a stray line. Tolerating that here is why this lives in one place
    instead of being re-implemented at each read site.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[[\s\S]*\]", text)
    if not match:
        return None
    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def live_alert_keys(store: dict, today: date, expiry_days: int, warn) -> tuple:
    """Keys whose alert is recent enough to widen the gate. Returns (keys, stats).

    Fails closed on any entry whose age cannot be established.
    """
    stats = {"alert_entries": 0, "alert_live": 0, "alert_expired": 0,
             "alert_undated": 0}
    if not isinstance(store, dict):
        warn("alert_matched.json is not an object — treating it as empty, so the "
             "60-point gate is inactive this run")
        return set(), stats

    cutoff = today - timedelta(days=expiry_days)
    live = set()
    for key, entry in store.items():
        stats["alert_entries"] += 1
        raw_date = (entry or {}).get("first_alerted") if isinstance(entry, dict) else None
        try:
            alerted = datetime.strptime(str(raw_date), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            # Undatable: fail closed. The job needs 75 like any other.
            stats["alert_undated"] += 1
            warn(f"alert entry {key} has no usable first_alerted ({raw_date!r}) — "
                 "not counted as alert-matched")
            continue
        if alerted < cutoff:
            stats["alert_expired"] += 1
            continue
        stats["alert_live"] += 1
        live.add(key)

    return live, stats


def prune_store(store: dict, live: set) -> dict:
    """The store with only live entries kept. Used solely under --prune."""
    return {k: v for k, v in store.items() if k in live}


def _score_of(job: dict) -> int:
    """The job's overall score as an int, or -1 when it is missing or unusable."""
    value = job.get("score", job.get("rank_score"))
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return -1


def apply_gate(jobs: list, live_keys, strong: int, min_score: int, cap: int,
               warn) -> tuple:
    """Re-apply the gate and the cap. Returns (kept, rejected, stats).

    `rejected` entries carry a `gate_note` saying why, for the report.
    """
    stats = {"ranker_returned": len(jobs), "gate_rejected": 0, "over_cap": 0,
             "unscored": 0, "alert_claim_corrected": 0, "cleared": 0}
    qualified = []

    for position, job in enumerate(jobs):
        if not isinstance(job, dict):
            stats["unscored"] += 1
            warn(f"ranker entry {position} is not an object — skipped")
            continue

        key = job.get("key") or ""
        score = _score_of(job)
        if score < 0:
            stats["unscored"] += 1
            warn(f"{job.get('title') or '(untitled)'} has no usable score "
                 f"({job.get('score')!r}) — cannot gate it, so it is not drafted")
            job["gate_note"] = "no usable score"
            stats["gate_rejected"] += 1
            qualified.append((None, job))
            continue

        # The store is the authority, not the ranker's self-report.
        alert_matched = key in live_keys
        if bool(job.get("alert_matched")) != alert_matched:
            stats["alert_claim_corrected"] += 1
            warn(f"{job.get('title') or '(untitled)'}: ranker said "
                 f"alert_matched={bool(job.get('alert_matched'))}, "
                 f"alert_matched.json says {alert_matched} — using the store")
        job["alert_matched"] = alert_matched

        if score >= strong:
            job["gate_reason"] = "score>=75"
        elif score >= min_score and alert_matched:
            job["gate_reason"] = "alert_matched+score>=60"
        else:
            job["gate_reason"] = None
            job["gate_note"] = (
                f"score {score} below {strong} and not alert-matched"
                if score >= min_score else f"score {score} below {min_score}")
            stats["gate_rejected"] += 1
            qualified.append((None, job))
            continue

        # Sort key: score desc, then alert-matched first, then richer evidence
        # (a Phase 1c full description) first, then ranker order for stability.
        rank = (-score, 0 if alert_matched else 1,
                0 if job.get("enriched") else 1,
                position)
        qualified.append((rank, job))

    kept = sorted((r, j) for r, j in qualified if r is not None)
    rejected = [j for r, j in qualified if r is None]

    if len(kept) > cap:
        stats["over_cap"] = len(kept) - cap
        warn(f"{len(kept)} jobs cleared the gate but the cap is {cap} — "
             f"{stats['over_cap']} moved to the not-drafted list. Raise "
             "pipeline.max_jobs_to_apply in config/automation.json to draft more.")
        for _, job in kept[cap:]:
            job["gate_note"] = f"cleared the gate but over the cap of {cap}"
            rejected.append(job)
        kept = kept[:cap]

    stats["cleared"] = len(kept)
    return [job for _, job in kept], rejected, stats


def to_not_drafted(job: dict) -> dict:
    """Trim a rejected job to the fields the report's table reads."""
    entry = {field: job.get(field) for field in CARRY_FIELDS}
    entry["gate_note"] = job.get("gate_note", "below the drafting gate")
    return entry


def merge_not_drafted(existing, rejected: list, warn) -> list:
    """The ranker's own not-drafted list plus the gate's rejections, deduped by key."""
    if existing is None:
        existing = []
    if not isinstance(existing, list):
        warn("the not-drafted file is not a JSON array — rebuilding it from the "
             "gate's rejections only")
        existing = []

    merged = [e for e in existing if isinstance(e, dict)]
    seen = {e.get("key") for e in merged if e.get("key")}
    for job in rejected:
        key = job.get("key")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        merged.append(to_not_drafted(job))
    return merged


def load_json(path: Path, what: str, warn, missing_ok=True):
    """Read JSON, or None. A missing file is normal for the alert store."""
    if not path.is_file():
        if not missing_ok:
            print(f"Error: {what} not found at {path}", file=sys.stderr)
        return None
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        warn(f"could not read {what} at {path}: {exc}")
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=Path, required=True,
                        help="Phase 2 output. Rewritten in place as a clean JSON array.")
    parser.add_argument("--not-drafted", type=Path, default=None,
                        help="Not-drafted list; gate rejections are appended to it.")
    parser.add_argument("--alerts", type=Path, default=DEFAULT_ALERTS,
                        help="alert_matched.json. Missing is normal: the 60 gate is then off.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG,
                        help="automation.json, read only for the pipeline caps.")
    parser.add_argument("--cap", type=int, default=None,
                        help="Override pipeline.max_jobs_to_apply.")
    parser.add_argument("--strong-score", type=int, default=STRONG_SCORE)
    parser.add_argument("--min-score", type=int, default=None,
                        help="Override pipeline.min_score_threshold.")
    parser.add_argument("--expiry-days", type=int, default=EXPIRY_DAYS,
                        help="An alert older than this stops widening the gate.")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD; drives expiry.")
    parser.add_argument("--prune", action="store_true",
                        help="Also drop expired entries from alert_matched.json.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report the decision without writing anything.")
    args = parser.parse_args()

    def warn(message):
        print(f"  gate: {message}", file=sys.stderr)

    try:
        today = (datetime.strptime(args.today, "%Y-%m-%d").date()
                 if args.today else date.today())
    except ValueError:
        print(f"Error: --today must be YYYY-MM-DD, got {args.today!r}", file=sys.stderr)
        return 1

    if not args.jobs.is_file():
        print(f"Error: could not read the ranked-jobs file at {args.jobs}",
              file=sys.stderr)
        return 1
    try:
        raw = args.jobs.read_text()
    except OSError as exc:
        print(f"Error: could not read {args.jobs}: {exc}", file=sys.stderr)
        return 1

    jobs = extract_array(raw)
    if jobs is None:
        # Loud, not silent: an unparseable ranker output and a genuine "nothing
        # cleared the gate" both end with zero documents, and they are not the
        # same event. The caller turns this into a report warning.
        print(f"Error: no JSON array in {args.jobs} — the ranker's output was not "
              "usable, so no documents can be gated", file=sys.stderr)
        if not args.dry_run:
            args.jobs.write_text("[]\n")
        print(json.dumps({"cleared": 0, "parse_failed": True}))
        return 1

    # Read only the two caps; this file also holds an SMTP password, which is
    # never read, logged, or echoed here.
    config = load_json(args.config, "automation.json", warn) or {}
    pipeline_cfg = config.get("pipeline", {}) if isinstance(config, dict) else {}
    cap = args.cap if args.cap is not None else int(
        pipeline_cfg.get("max_jobs_to_apply", DEFAULT_CAP))
    min_score = args.min_score if args.min_score is not None else int(
        pipeline_cfg.get("min_score_threshold", MIN_SCORE))
    cap = max(0, cap)

    store = load_json(args.alerts, "alert_matched.json", warn)
    if store is None:
        store = {}
        warn(f"no alert store at {args.alerts} — every job needs "
             f"{args.strong_score}+ this run (this is normal until "
             "/linkedin-alerts has run)")
    live_keys, alert_stats = live_alert_keys(store, today, args.expiry_days, warn)

    kept, rejected, stats = apply_gate(jobs, live_keys, args.strong_score,
                                       min_score, cap, warn)
    summary = {**stats, **alert_stats, "not_drafted_added": len(rejected),
               "cap": cap, "strong_score": args.strong_score,
               "min_score": min_score}

    if args.dry_run:
        for job in kept:
            print(f"  gate: would draft {job.get('title')} @ {job.get('company')} "
                  f"({job.get('score')}, {job.get('gate_reason')})", file=sys.stderr)
        print(json.dumps({**summary, "dry_run": True}))
        return 0

    with open(args.jobs, "w") as handle:
        json.dump(kept, handle, indent=2)
        handle.write("\n")

    if args.not_drafted and rejected:
        existing = load_json(args.not_drafted, "the not-drafted file", warn)
        merged = merge_not_drafted(existing, rejected, warn)
        with open(args.not_drafted, "w") as handle:
            json.dump(merged, handle, indent=2)
            handle.write("\n")

    if args.prune and alert_stats["alert_expired"] + alert_stats["alert_undated"]:
        pruned = prune_store(store, live_keys)
        with open(args.alerts, "w") as handle:
            json.dump(pruned, handle, indent=2)
            handle.write("\n")
        warn(f"pruned {len(store) - len(pruned)} expired entries from {args.alerts}")

    print(json.dumps(summary))
    print(f"Gate: {summary['cleared']}/{summary['ranker_returned']} jobs cleared "
          f"(score >= {args.strong_score}, or >= {min_score} if alert-matched; "
          f"{alert_stats['alert_live']} live alerts, "
          f"{alert_stats['alert_expired']} expired; "
          f"{stats['gate_rejected']} rejected, {stats['over_cap']} over cap)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
