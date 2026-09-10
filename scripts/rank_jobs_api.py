#!/usr/bin/env python3
"""Rank fetched jobs with one direct Anthropic-compatible Messages API request."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Mapping


DEFAULT_BASE_URL = "https://api.anthropic.com"
SCORE_FIELDS = ("technical", "experience", "behavioral", "career")
SCORE_WEIGHTS = (0.30, 0.25, 0.15, 0.30)
GATE_VALUES = {"PASS", "FLAG", "FAIL"}


class RankingConfigError(Exception):
    """Runtime provider configuration is incomplete or invalid."""


class RankingRequestError(Exception):
    """The provider request failed without exposing sensitive details."""


class RankingResponseError(Exception):
    """The provider returned an unusable ranking response."""


@dataclass(frozen=True)
class RankingConfig:
    base_url: str
    model: str
    api_key: str


def _setting(settings: Mapping[str, object], name: str) -> str:
    env = settings.get("env", {}) if isinstance(settings, Mapping) else {}
    value = env.get(name, "") if isinstance(env, Mapping) else ""
    return value.strip() if isinstance(value, str) else ""


def load_config(
    environ: Mapping[str, str],
    project_settings: Mapping[str, object],
    model_override: str | None = None,
) -> RankingConfig:
    api_key = (
        environ.get("ANTHROPIC_API_KEY", "").strip()
        or environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        or _setting(project_settings, "ANTHROPIC_API_KEY")
        or _setting(project_settings, "ANTHROPIC_AUTH_TOKEN")
    )
    model = (
        (model_override or "").strip()
        or environ.get("ANTHROPIC_MODEL", "").strip()
        or _setting(project_settings, "ANTHROPIC_MODEL")
    )
    base_url = (
        environ.get("ANTHROPIC_BASE_URL", "").strip()
        or _setting(project_settings, "ANTHROPIC_BASE_URL")
        or DEFAULT_BASE_URL
    )
    if not api_key:
        raise RankingConfigError("ranking API credential is not configured")
    if not model:
        raise RankingConfigError("ranking model is not configured")
    return RankingConfig(base_url, model, api_key)


def messages_endpoint(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1/messages"):
        return root
    if root.endswith("/v1"):
        return root + "/messages"
    return root + "/v1/messages"


def request_message(
    config: RankingConfig,
    ranking_prompt: str,
    timeout: float,
    *,
    opener=urllib.request,
) -> str:
    payload = json.dumps({
        "model": config.model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": ranking_prompt}],
    }).encode("utf-8")
    request = urllib.request.Request(
        messages_endpoint(config.base_url),
        data=payload,
        headers={
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            document = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RankingRequestError(
            f"ranking API request failed with HTTP status {exc.code}"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RankingRequestError("ranking API request failed") from None
    except ValueError:
        raise RankingResponseError("ranking API response is not valid JSON") from None

    content = document.get("content") if isinstance(document, dict) else None
    if not isinstance(content, list) or not content:
        raise RankingResponseError("ranking API response has no text content")
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text.strip():
            return text
    raise RankingResponseError("ranking API response has no text content")


def _valid_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_text_list(value: object) -> bool:
    return isinstance(value, list) and all(_valid_text(item) for item in value)


def _validate_gate(value: object) -> bool:
    return isinstance(value, str) and value in GATE_VALUES


def validate_decisions(text: str, jobs: list[dict]) -> list[dict]:
    try:
        document = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise RankingResponseError("ranking model returned invalid JSON") from None
    results = document.get("results") if isinstance(document, dict) else None
    if not isinstance(results, list):
        raise RankingResponseError("ranking model response must contain a results array")

    expected = [job.get("dedup_key") for job in jobs]
    if any(not _valid_text(key) for key in expected) or len(set(expected)) != len(expected):
        raise RankingResponseError("ranking input contains invalid or duplicate keys")
    keys = [result.get("key") if isinstance(result, dict) else None for result in results]
    if len(keys) != len(expected) or len(set(keys)) != len(keys) or set(keys) != set(expected):
        raise RankingResponseError("ranking results do not exactly match input keys")

    for result in results:
        decision = result.get("decision")
        if decision not in {"score", "drop"}:
            raise RankingResponseError("ranking result has an invalid decision")
        if not _validate_gate(result.get("location_gate")) or not _validate_gate(
            result.get("language_gate")
        ):
            raise RankingResponseError("ranking result has an invalid gate verdict")
        if decision == "drop":
            if not _valid_text(result.get("drop_reason")):
                raise RankingResponseError("drop decision requires a reason")
            continue

        scores = result.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(SCORE_FIELDS):
            raise RankingResponseError("score decision has invalid dimensions")
        for value in scores.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise RankingResponseError("score values must be numbers")
            if value < 0 or value > 100:
                raise RankingResponseError("score values must be between 0 and 100")
        if not _valid_text(result.get("track")):
            raise RankingResponseError("score decision requires a track")
        if not _valid_text_list(result.get("strengths")):
            raise RankingResponseError("score decision requires textual strengths")
        if not _valid_text_list(result.get("gaps")):
            raise RankingResponseError("score decision requires textual gaps")
    return results


def _escaped_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).replace(
        "<", "\\u003c"
    )


def build_prompt(
    evaluation: str,
    profile: str,
    prompt_template: str,
    jobs: list[dict],
    seen: dict,
    alerts: dict,
    tracker: list[dict],
    today: str,
) -> str:
    return "\n\n".join((
        prompt_template,
        "## Trusted evaluation rules\n" + evaluation,
        "## Trusted candidate profile\n" + profile,
        "## Trusted local context\n" + _escaped_json({
            "today": today, "seen_jobs": seen, "alert_matches": alerts,
            "tracker_rows": tracker,
        }),
        "## Untrusted job postings\n<untrusted_jobs>" + _escaped_json(jobs)
        + "</untrusted_jobs>",
    ))


def _overall(scores: dict) -> float:
    value = sum(scores[name] * weight for name, weight in zip(SCORE_FIELDS, SCORE_WEIGHTS))
    return round(value, 1)


def _verdict(score: float) -> str:
    if score >= 75:
        return "Strong Fit"
    if score >= 60:
        return "Good Fit"
    if score >= 45:
        return "Moderate Fit"
    if score >= 30:
        return "Weak Fit"
    return "Poor Fit"


def _alert_keys(alerts: object, today: str, expiry_days: int = 30) -> set[str]:
    if not isinstance(alerts, dict):
        return set()
    try:
        current = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        return set()
    live = set()
    for key, entry in alerts.items():
        raw = entry.get("first_alerted") if isinstance(entry, dict) else None
        try:
            first = datetime.strptime(str(raw), "%Y-%m-%d").date()
        except ValueError:
            continue
        if 0 <= (current - first).days <= expiry_days:
            live.add(key)
    return live


def derive_outputs(
    jobs: list[dict],
    decisions: list[dict],
    seen_store: object,
    alerts: object,
    today: str,
) -> tuple[list[dict], list[dict], dict]:
    seen = dict(seen_store) if isinstance(seen_store, dict) else {}
    seen_entries = seen.get("seen")
    seen["seen"] = dict(seen_entries) if isinstance(seen_entries, dict) else {}
    jobs_by_key = {job["dedup_key"]: job for job in jobs}
    live_alerts = _alert_keys(alerts, today)
    ranked = []
    not_drafted = []

    for decision in decisions:
        if decision["decision"] == "drop":
            continue
        key = decision["key"]
        job = jobs_by_key[key]
        score = _overall(decision["scores"])
        verdict = _verdict(score)
        alert_matched = key in live_alerts
        gate_reason = (
            "score>=75" if score >= 75 else
            "alert_matched+score>=60" if score >= 60 and alert_matched else None
        )
        result = {
            "key": key,
            "title": job.get("title"),
            "company": job.get("company"),
            "url": job.get("url"),
            "location": job.get("location"),
            "portal": job.get("portal"),
            "track": decision["track"],
            "score": score,
            "verdict": verdict,
            "scores": decision["scores"],
            "alert_matched": alert_matched,
            "gate_reason": gate_reason,
            "strengths": decision["strengths"],
            "gaps": decision["gaps"],
            "location_gate": decision["location_gate"],
            "language_gate": decision["language_gate"],
            "posting_text": job.get("description") or job.get("description_snippet") or "",
        }
        if "repeat" in job:
            result["repeat"] = job["repeat"]
        if gate_reason:
            ranked.append(result)
        elif score >= 60:
            not_drafted.append({
                field: result.get(field) for field in
                ("key", "title", "company", "url", "location", "portal", "track", "score", "verdict")
            })
        seen["seen"][key] = {
            "status": "ranked", "rank_score": score, "rank_verdict": verdict,
            "rank_date": today, "location": decision["location_gate"],
            "portal": job.get("portal"), "track": decision["track"],
            "alert_matched": alert_matched, "url": job.get("url"),
        }

    ranked.sort(key=lambda item: (-item["score"], not item["alert_matched"], not bool(jobs_by_key[item["key"]].get("description"))))
    not_drafted.sort(key=lambda item: -item["score"])
    return ranked, not_drafted, seen


def _write_temp(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def write_outputs(
    output: Path,
    not_drafted_path: Path,
    seen_path: Path,
    ranked: list[dict],
    not_drafted: list[dict],
    seen: dict,
    *,
    replace: Callable[[os.PathLike, os.PathLike], None] = os.replace,
) -> None:
    targets = ((output, ranked), (not_drafted_path, not_drafted), (seen_path, seen))
    temporaries: list[tuple[Path, Path]] = []
    try:
        for target, value in targets:
            temporaries.append((_write_temp(target, value), target))
        for temporary, target in temporaries:
            replace(temporary, target)
            os.chmod(target, 0o600)
    finally:
        for temporary, _ in temporaries:
            temporary.unlink(missing_ok=True)


def _load_json(path: Path, *, missing: object = None) -> object:
    if not path.is_file():
        return missing
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise RankingConfigError(f"could not read required JSON input: {path.name}") from None


def _load_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise RankingConfigError(f"could not read required input: {path.name}") from None


def _load_tracker(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        raise RankingConfigError("could not read tracker input") from None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--not-drafted", type=Path, required=True)
    parser.add_argument("--seen", type=Path, required=True)
    parser.add_argument("--alerts", type=Path, required=True)
    parser.add_argument("--tracker", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--model")
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--request-timeout", type=float, default=120.0)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, environ: Mapping[str, str] = os.environ) -> None:
    settings = _load_json(args.settings, missing={})
    if not isinstance(settings, dict):
        raise RankingConfigError("project settings must be a JSON object")
    config = load_config(environ, settings, args.model)
    job_document = _load_json(args.jobs)
    jobs = job_document.get("results") if isinstance(job_document, dict) else None
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise RankingConfigError("jobs input must contain a results array")
    seen = _load_json(args.seen, missing={"seen": {}})
    alerts = _load_json(args.alerts, missing={})
    tracker = _load_tracker(args.tracker)
    prompt = build_prompt(
        _load_text(args.evaluation), _load_text(args.profile), _load_text(args.prompt),
        jobs, seen, alerts, tracker, args.today,
    )
    response = request_message(config, prompt, args.request_timeout)
    decisions = validate_decisions(response, jobs)
    ranked, not_drafted, updated_seen = derive_outputs(
        jobs, decisions, seen, alerts, args.today,
    )
    write_outputs(
        args.output, args.not_drafted, args.seen,
        ranked, not_drafted, updated_seen,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        run(parse_args(argv))
    except (RankingConfigError, RankingRequestError, RankingResponseError, OSError) as exc:
        print(f"ranking helper: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
