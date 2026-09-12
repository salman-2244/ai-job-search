#!/usr/bin/env python3
"""Rank fetched jobs with one direct Anthropic-compatible Messages API request."""

from __future__ import annotations

import argparse
import csv
import json
import os
import ssl
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
# The one verdict this module now *acts* on rather than merely spells-checks. Named
# so the enforcement in `derive_outputs` cannot drift from the validator above.
FAIL_VERDICT = "FAIL"


class RankingConfigError(Exception):
    """Runtime provider configuration is incomplete or invalid."""


class RankingRequestError(Exception):
    """The provider request failed without exposing sensitive details."""


class RankingResponseError(Exception):
    """The provider returned an unusable ranking response."""


# How the credential is presented on the wire. Anthropic and every gateway that
# speaks its Messages API accept two different credential kinds and they are NOT
# interchangeable: a first-party key goes in `x-api-key`, and an OAuth-style or
# gateway-issued token goes in `Authorization: Bearer`. Sending the second one in
# the first one's header is a 401, not a fallback.
AUTH_HEADER = "x-api-key"
AUTH_BEARER = "bearer"

# What this client calls itself. Configurable because some Anthropic-compatible
# gateways refuse unrecognised clients outright: agentrouter.org answers this exact
# string with 401 `unauthorized_client_error` while accepting a `claude-cli/...`
# one, same token, same endpoint. That is a decision about how to represent
# ourselves to a third party, so it is Salman's to make in settings, not a default
# baked in here. Set RANK_USER_AGENT in the environment or in .claude/settings.json.
DEFAULT_USER_AGENT = "ai-job-search-stage3/1.0"


@dataclass(frozen=True)
class RankingConfig:
    base_url: str
    model: str
    api_key: str
    #: Defaults to the first-party scheme so a 3-argument construction keeps the
    #: behaviour it had before this field existed.
    auth_scheme: str = AUTH_HEADER
    user_agent: str = DEFAULT_USER_AGENT


def _setting(settings: Mapping[str, object], name: str) -> str:
    env = settings.get("env", {}) if isinstance(settings, Mapping) else {}
    value = env.get(name, "") if isinstance(env, Mapping) else ""
    return value.strip() if isinstance(value, str) else ""


def load_config(
    environ: Mapping[str, str],
    project_settings: Mapping[str, object],
    model_override: str | None = None,
) -> RankingConfig:
    # The slot a credential arrives in decides the header it leaves in. This
    # ordering is the same one Claude Code itself applies, so a settings.json that
    # works for the CLI works here without a second, differently-shaped config.
    #
    # Getting this wrong is silent and total: before 2026-09-12 every slot was read
    # and every one of them was then sent as `x-api-key`. Salman's gateway
    # credential lives in ANTHROPIC_AUTH_TOKEN, so the ranking call had never once
    # succeeded — it 401'd, run_daily.sh classified 401 as non-retryable "auth/quota",
    # and the run reported "class quota" as though a balance had run out.
    api_key = ""
    auth_scheme = AUTH_HEADER
    for candidate, scheme in (
        (environ.get("ANTHROPIC_API_KEY", "").strip(), AUTH_HEADER),
        (environ.get("ANTHROPIC_AUTH_TOKEN", "").strip(), AUTH_BEARER),
        (_setting(project_settings, "ANTHROPIC_API_KEY"), AUTH_HEADER),
        (_setting(project_settings, "ANTHROPIC_AUTH_TOKEN"), AUTH_BEARER),
    ):
        if candidate:
            api_key = candidate
            auth_scheme = scheme
            break
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
    user_agent = (
        environ.get("RANK_USER_AGENT", "").strip()
        or _setting(project_settings, "RANK_USER_AGENT")
        or DEFAULT_USER_AGENT
    )
    if not api_key:
        raise RankingConfigError("ranking API credential is not configured")
    if not model:
        raise RankingConfigError("ranking model is not configured")
    return RankingConfig(base_url, model, api_key, auth_scheme, user_agent)


def messages_endpoint(base_url: str) -> str:
    root = base_url.rstrip("/")
    if root.endswith("/v1/messages"):
        return root
    if root.endswith("/v1"):
        return root + "/messages"
    return root + "/v1/messages"


def _default_opener():
    paths = ssl.get_default_verify_paths()
    if paths.cafile or paths.capath:
        return urllib.request.build_opener()
    system_ca = Path("/etc/ssl/cert.pem")
    if not system_ca.is_file():
        return urllib.request.build_opener()
    context = ssl.create_default_context(cafile=str(system_ca))
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))


def request_message(
    config: RankingConfig,
    ranking_prompt: str,
    timeout: float,
    *,
    opener=None,
) -> str:
    if opener is None:
        opener = _default_opener()
    payload = json.dumps({
        "model": config.model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": ranking_prompt}],
    }).encode("utf-8")
    # Exactly one credential header. Sending both would leak the token into a
    # header the gateway may log differently, and some gateways reject the pair
    # outright rather than picking one.
    if config.auth_scheme == AUTH_BEARER:
        credential_header = {"Authorization": f"Bearer {config.api_key}"}
    else:
        credential_header = {AUTH_HEADER: config.api_key}
    request = urllib.request.Request(
        messages_endpoint(config.base_url),
        data=payload,
        headers={
            **credential_header,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": config.user_agent,
        },
        method="POST",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            document = json.load(response)
    except urllib.error.HTTPError as exc:
        # The body, not just the code. Swallowing it cost a full day of diagnosis on
        # 2026-09-12: the status alone said 401, run_daily.sh classified that as
        # "auth/quota", and the run reported an exhausted balance — while the body
        # said `unauthorized_client_error: unauthorized client detected`, which is a
        # rejected User-Agent and has nothing to do with the credential or the
        # balance. Truncated because a gateway can return an HTML error page, and
        # read defensively because a body is not guaranteed. The request is never
        # echoed, so the credential cannot reach the log through here.
        try:
            detail = exc.read()[:300].decode("utf-8", "replace").strip().replace("\n", " ")
        except Exception:  # noqa: BLE001 - a body we cannot read must not mask the status
            detail = ""
        raise RankingRequestError(
            f"ranking API request failed with HTTP status {exc.code}"
            + (f": {detail}" if detail else "")
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
    dropped = []

    for decision in decisions:
        # LAW 0. A gate verdict is a verdict, not an annotation.
        #
        # Until 2026-09-12 this loop copied `location_gate` and `language_gate` into
        # the result dict (below) and never compared either to FAIL. `"FAIL"` appeared
        # exactly once in this module — in the `GATE_VALUES` spelling check — so a
        # reply of {"decision":"score","location_gate":"FAIL"} was scored, gated,
        # drafted and delivered exactly like a clean one. The model was being asked
        # the right question on every run and its answer was thrown away, which is the
        # whole reason "European countries only" and the language rule never bit.
        #
        # Coerced rather than raised: having `validate_decisions` reject the batch
        # would sink 25 good jobs over one inconsistent row, and the safe reading of
        # "score 82, location FAIL" is unambiguous — the gate wins. The coercion is
        # recorded so a model that starts doing this often stays visible.
        gate_failed = [name[: -len("_gate")]
                       for name in ("location_gate", "language_gate")
                       if str(decision.get(name) or "").upper() == FAIL_VERDICT]
        if decision["decision"] == "drop" or gate_failed:
            reason = decision.get("drop_reason") or ""
            if gate_failed and decision["decision"] != "drop":
                reason = (f"{', '.join(gate_failed)} gate returned FAIL alongside a "
                          "score decision - the gate wins"
                          + (f"; {reason}" if reason else ""))
            job = jobs_by_key[decision["key"]]
            dropped.append({
                "key": decision["key"],
                "title": job.get("title"),
                "company": job.get("company"),
                "location": job.get("location"),
                "portal": job.get("portal"),
                "url": job.get("url"),
                "drop_reason": reason,
                "location_gate": decision.get("location_gate"),
                "language_gate": decision.get("language_gate"),
                "coerced": bool(gate_failed and decision["decision"] != "drop"),
            })
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
    # Every exclusion names itself. The funnel's standing rule is that nothing is cut
    # silently (deferred lists, closest-miss tables, unverified-gate surfacing all
    # exist for this), and drops were the one cut that vanished without a trace — the
    # model's `drop_reason` was parsed, validated as non-empty, and then discarded by
    # the `continue` this block replaced. stderr rather than stdout so the line lands
    # in the run log without touching any consumer that parses stdout.
    for item in dropped:
        print(f"[rank] dropped {item['company']} — {item['title']} "
              f"({item.get('location') or 'location unstated'}): {item['drop_reason']}"
              + ("  [gate-coerced]" if item["coerced"] else ""),
              file=sys.stderr)
    if dropped:
        print(f"[rank] {len(dropped)} of {len(decisions)} job(s) dropped "
              f"({sum(1 for d in dropped if d['coerced'])} by gate coercion)",
              file=sys.stderr)
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
