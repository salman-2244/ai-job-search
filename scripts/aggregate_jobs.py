#!/usr/bin/env python3
"""Merge multiple portal CLI JSON outputs into a unified job array.

Usage:
    python aggregate_jobs.py portal1.json portal2.json ... > unified_jobs.json

Each input file is the JSON output from a portal CLI's `search --format json`.
The script normalizes the schema, deduplicates by URL and company+title,
and writes a unified JSON array to stdout.
"""

import json
import re
import sys
from pathlib import Path

# LinkedIn serves the same posting from country subdomains (hu., de., nl.linkedin.com)
# and with varying trailing slug text, so URL-based dedup lets one job enter several
# times under different keys. The numeric job ID at the end of the slug is canonical.
#
# `/comm/` is optional because that is the path LinkedIn uses in the job-alert emails
# Phase 0b reads (`linkedin.com/comm/jobs/view/<id>?midToken=...`). Without it those
# URLs key as `url:https://www.linkedin.com/comm/jobs/view/<id>` — a different key from
# the `url:linkedin:<id>` a search page produces for the very same posting. The alert
# parser canonicalizes what it emits, so this is the safety net: no path can turn one
# posting into two corpus entries, and no alert key can end up unable to join the job
# it was alerting about (which would silently disable the 60-point gate).
LINKEDIN_JOB_ID = re.compile(
    r"^https?://(?:[a-z0-9-]+\.)*linkedin\.com/(?:comm/)?jobs/view/(?:.*?-)?(\d{6,})(?:[/?#]|$)",
    re.IGNORECASE,
)


def normalize_job(raw: dict, portal: str) -> dict:
    """Normalize a portal-specific job result into the unified schema."""
    title = (raw.get("title") or "").strip()
    company = (raw.get("company") or "").strip() or None
    url = (raw.get("url") or "").strip()
    location = (raw.get("location") or "").strip() or None
    date = raw.get("date")
    description = raw.get("description")

    # Truncate description to a snippet (first 500 chars) for the unified output
    snippet = None
    if description:
        snippet = description[:500].strip()
        if len(description) > 500:
            snippet += "..."

    return {
        "title": title,
        "company": company,
        "url": url,
        "location": location,
        "date_posted": date,
        "portal": portal,
        "description_snippet": snippet,
    }


def detect_portal(file_path: Path, data: dict) -> str:
    """Detect which portal produced this JSON based on file path or content."""
    # Try to detect from file path
    path_str = str(file_path).lower()
    # Checked before "linkedin" on purpose: that branch matches any path *containing*
    # "linkedin", so the alert file would otherwise report as linkedin-search and the
    # report could not distinguish a job Salman's own alert surfaced from one a search
    # query found. The two deserve different attention, which is the whole point.
    if "linkedin-alert" in path_str or "linkedin_alert" in path_str:
        return "linkedin-alert"
    if "linkedin" in path_str:
        return "linkedin-search"
    if "freehire" in path_str:
        return "freehire-search"
    if "arbeitnow" in path_str:
        return "arbeitnow-search"
    if "weworkremotely" in path_str or "wwr" in path_str:
        return "weworkremotely-search"

    # Try to detect from content structure
    if "scan_capped" in data.get("meta", {}):
        return "arbeitnow-search"
    if "results" in data and data["results"]:
        sample = data["results"][0]
        if "work_mode" in sample:
            return "freehire-search"
        if "remote" in sample:
            return "arbeitnow-search"
        if "category" in sample:
            return "weworkremotely-search"
        if "companyUrl" in sample:
            return "linkedin-search"

    return "unknown"


def make_dedup_key(job: dict) -> str:
    """Create a stable dedup key from URL or company+title.

    LinkedIn URLs collapse to `url:linkedin:<jobId>` so the same posting served from
    `hu.linkedin.com`, `de.linkedin.com`, or with a different slug is recognized as one
    job. Everything else keys on the URL with its query string stripped.
    """
    url = job.get("url", "").strip()
    if url:
        linkedin = LINKEDIN_JOB_ID.match(url)
        if linkedin:
            return f"url:linkedin:{linkedin.group(1)}"
        # Strip UTM parameters and trailing slashes for dedup
        clean_url = url.split("?")[0].rstrip("/")
        return f"url:{clean_url}"

    company = (job.get("company") or "").lower().strip()
    title = (job.get("title") or "").lower().strip()
    return f"ct:{company}|{title}"


def main():
    if len(sys.argv) < 2:
        print("Usage: aggregate_jobs.py <portal1.json> [portal2.json ...]", file=sys.stderr)
        sys.exit(1)

    all_jobs = []
    seen_keys = set()
    stats = {"total_input": 0, "unique": 0, "dupes": 0, "portals": {}}

    for arg in sys.argv[1:]:
        file_path = Path(arg)
        if not file_path.exists():
            print(f"Warning: {arg} not found, skipping", file=sys.stderr)
            continue

        try:
            with open(file_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: {arg} failed to parse: {e}", file=sys.stderr)
            continue

        portal = detect_portal(file_path, data)
        results = data.get("results", [])

        portal_count = 0
        for raw in results:
            stats["total_input"] += 1
            job = normalize_job(raw, portal)

            if not job["url"] and not (job["company"] and job["title"]):
                continue  # Skip jobs with no URL and no company+title

            key = make_dedup_key(job)
            if key in seen_keys:
                stats["dupes"] += 1
                continue

            # Publish the key on the job itself. The ranker prompt tells the model to
            # reuse the key "exactly as it appears in the input file's results" so that
            # seen_jobs.json stays consistent with this script; without the field that
            # instruction is unfollowable and the model has to re-derive the key.
            job["dedup_key"] = key

            seen_keys.add(key)
            all_jobs.append(job)
            stats["unique"] += 1
            portal_count += 1

        # Accumulate, never assign: several input files map to the same portal (a
        # matrix run produces many linkedin_* files), so `=` would report only the
        # last file's count and silently understate every portal's contribution.
        stats["portals"][portal] = stats["portals"].get(portal, 0) + portal_count

    # Output
    output = {
        "meta": {
            "total_input": stats["total_input"],
            "unique": stats["unique"],
            "dupes_skipped": stats["dupes"],
            "portals": stats["portals"],
        },
        "results": all_jobs,
    }

    json.dump(output, sys.stdout, indent=2)
    print()  # Trailing newline

    # Summary to stderr
    print(f"Aggregated: {stats['total_input']} input -> {stats['unique']} unique ({stats['dupes']} dupes)", file=sys.stderr)
    for portal, count in stats["portals"].items():
        print(f"  {portal}: {count}", file=sys.stderr)


if __name__ == "__main__":
    main()
