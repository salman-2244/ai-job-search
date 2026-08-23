#!/usr/bin/env python3
"""One-time migration: canonicalize the keys in job_scraper/seen_jobs.json.

Usage:
    python3 scripts/migrate_seen_jobs.py [--file PATH] [--dry-run]

Why this exists
---------------
`aggregate_jobs.py` keys jobs canonically: `url:linkedin:<jobId>` for LinkedIn
postings (so the same job served from `hu.`, `de.`, and `nl.linkedin.com` is one
job), `url:<url-without-query-string>` for everything else, `ct:<company>|<title>`
when there is no URL. The history file predates that scheme: its keys are **raw
URLs**, query strings and country subdomains included.

Left alone, the two schemes never meet. Every historical entry would fail to match
the key the pipeline now derives, so all of them would re-enter as new jobs, be
re-ranked, and could be re-drafted.

The URL must survive the rewrite
--------------------------------
These entries carry only `status`, `rank_score`, `rank_verdict`, `rank_date`, and
`location`. There is **no `url` field** — the key is the only copy of the URL. Two
consumers depend on that URL:

  - `/rank` (`.claude/commands/rank.md:126`) links "the entry's `url` field".
  - The health check (`job-scraper/SKILL.md:183`) attributes portal-less entries
    "by matching the URL's domain against each portal's base URL".

So this migration writes the original URL into a real `url` field. Without that,
canonicalizing the key would destroy the only copy and break both consumers.

Deliberately NOT backfilling `portal`
-------------------------------------
`job-scraper/SKILL.md:146` states that entries predating the `portal` field are
attributed by domain matching and says "do not backfill". This migration honors
that: it preserves the URL that domain matching needs rather than writing a guessed
`portal` value where a derivation already exists.

Safety
------
A collision (two historical keys canonicalizing to one) would silently merge two
jobs' rank history, so the migration **aborts** instead of choosing a winner. It
also refuses to write unless the entry count is unchanged, and takes a timestamped
backup first. Re-running it is a no-op: already-canonical keys are left alone.
"""

import argparse
import importlib.util
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_FILE = REPO / "job_scraper" / "seen_jobs.json"

_spec = importlib.util.spec_from_file_location(
    "aggregate_jobs", REPO / "scripts" / "aggregate_jobs.py"
)
_agg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_agg)

# The single source of truth for key derivation. Importing it rather than
# reimplementing it is the point: a second copy would drift from the pipeline's.
make_dedup_key = _agg.make_dedup_key

CANONICAL_PREFIXES = ("url:", "ct:")


def is_canonical(key: str) -> bool:
    """True when a key is already in the pipeline's format."""
    return key.startswith(CANONICAL_PREFIXES)


def url_of(key: str, entry: dict) -> str:
    """Recover the posting URL for an entry.

    The stored `url` field wins when present. Otherwise the key itself is the URL,
    which is the case for every pre-migration entry.
    """
    stored = (entry.get("url") or "").strip()
    if stored:
        return stored
    if key.startswith(("http://", "https://")):
        return key
    return ""


def migrate_entries(seen: dict, warn) -> dict:
    """Return a new `seen` map with canonical keys and a preserved `url` field.

    Raises ValueError on a key collision — merging two jobs' rank history silently
    is worse than stopping.
    """
    out = {}
    origins = {}

    for key, entry in seen.items():
        if not isinstance(entry, dict):
            warn(f"skipping {key!r}: entry is {type(entry).__name__}, not an object")
            continue

        new_entry = dict(entry)
        url = url_of(key, entry)

        if url:
            # Preserve the URL before the key stops carrying it.
            if not (entry.get("url") or "").strip():
                new_entry["url"] = url
            new_key = make_dedup_key({"url": url})
        elif is_canonical(key):
            new_key = key
        else:
            # No URL and not canonical: fall back to company+title, which is what
            # the pipeline does for URL-less jobs.
            new_key = make_dedup_key({
                "url": "",
                "company": entry.get("company", ""),
                "title": entry.get("title", ""),
            })
            warn(f"{key!r} has no URL; keyed on company+title as {new_key!r}")

        if new_key in out:
            raise ValueError(
                f"key collision: {key!r} and {origins[new_key]!r} both canonicalize "
                f"to {new_key!r}. Two jobs' rank history would be merged; resolve by "
                "hand before migrating."
            )

        out[new_key] = new_entry
        origins[new_key] = key

    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_FILE)
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would change and write nothing.")
    args = parser.parse_args()

    def warn(msg):
        print(f"Warning: {msg}", file=sys.stderr)

    if not args.file.is_file():
        print(f"Nothing to migrate: {args.file} does not exist.", file=sys.stderr)
        return 0

    try:
        raw = json.loads(args.file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"Error: could not read {args.file}: {e}", file=sys.stderr)
        return 1

    if not isinstance(raw, dict) or not isinstance(raw.get("seen"), dict):
        print(f"Error: {args.file} is not of the form {{\"seen\": {{...}}}}",
              file=sys.stderr)
        return 1

    seen = raw["seen"]
    before = len(seen)
    already = sum(1 for k in seen if is_canonical(k))

    try:
        migrated = migrate_entries(seen, warn)
    except ValueError as e:
        print(f"ABORTED: {e}", file=sys.stderr)
        return 1

    after = len(migrated)
    if after != before:
        print(f"ABORTED: entry count changed {before} -> {after}. Refusing to write.",
              file=sys.stderr)
        return 1

    changed = sum(1 for k in migrated if k not in seen)
    urls_added = sum(1 for k, v in migrated.items()
                     if v.get("url") and not seen.get(k, {}).get("url"))

    print(f"{before} entries: {changed} keys rewritten, {already} already canonical, "
          f"{urls_added} url fields preserved.", file=sys.stderr)

    if args.dry_run:
        print("Dry run — nothing written.", file=sys.stderr)
        return 0

    if changed == 0 and urls_added == 0:
        print("Already migrated; nothing to write.", file=sys.stderr)
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = args.file.with_suffix(f".json.pre-migration-{stamp}")
    shutil.copy2(args.file, backup)

    raw["seen"] = migrated
    args.file.write_text(json.dumps(raw, indent=2) + "\n")

    print(f"Migrated {args.file} (backup: {backup.name})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
