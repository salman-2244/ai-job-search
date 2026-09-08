"""Stage 3 configuration: where the bot's secrets live and how they are validated.

No token, password or chat id is ever written into this repo. Everything is read at
startup from a KEY=VALUE file outside the tree, defaulting to
`~/.jobsearch-stage3.env` and overridable with `JOBSEARCH_STAGE3_ENV`. This mirrors
`scripts/telegram_select.py`'s `~/.jobsearch-selector.env`, and for the same reason
`docs/TELEGRAM.md` gives: "never copy it into this repo".

    STAGE3_BOT_TOKEN=123456:AA...        a *third* bot from @BotFather
    STAGE3_CHAT_ID=123456789             where unsolicited messages go
    STAGE3_ALLOWED_USER_IDS=123456789    comma-separated; nobody else is answered
    LINKEDIN_EMAIL=you@example.com       optional, tier-3 enrichment only
    LINKEDIN_PASSWORD=...                optional, tier-3 enrichment only
    STAGE3_REPO=/Users/you/Projects/...  optional, defaults to this checkout
    STAGE3_MAX_RUNTIME=10800             optional seconds; 0 disables the ceiling

Why a third token and not the two that already exist: Telegram hands `getUpdates` to
exactly one consumer per token. The Claude Code bot polls continuously under launchd,
and `telegram_select.py` polls `SELECTOR_BOT_TOKEN` while it waits for a job selection
— which happens *during* a run this bot is supervising. A second poller on either token
would 409 and the two would race to steal each other's updates, costing phone access to
Claude or silently eating a selection. `validate_token_isolation` refuses to start in
that configuration rather than discovering it at runtime.

Secrets are handled under three rules:
  - never in argv (`ps` is world-readable), so they are passed to children through the
    environment, not the command line;
  - never in a log or an exception message — `redact` exists for the cases where a
    value must be mentioned at all;
  - never returned by `__repr__`; `Stage3Config` overrides it.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = Path.home() / ".jobsearch-stage3.env"
SELECTOR_ENV_PATH = Path.home() / ".jobsearch-selector.env"
CLAUDE_BOT_ENV_PATH = Path.home() / "claude-code-telegram" / ".env"

#: Keys whose values must never be logged, echoed or included in an exception.
SECRET_KEYS = frozenset({
    "STAGE3_BOT_TOKEN", "LINKEDIN_PASSWORD", "SELECTOR_BOT_TOKEN",
    "TELEGRAM_BOT_TOKEN", "ANTHROPIC_API_KEY",
})

#: Seconds a run may take before the orchestrator stops it. Three hours: the observed
#: worst case is the 2026-08-18 run at ~3.5h, which is exactly the shape of hang this
#: ceiling exists to end, and the script's own per-phase watchdogs bound everything
#: shorter. 0 disables it.
DEFAULT_MAX_RUNTIME = 10800
DEFAULT_RUN_STATE_ROOT = Path.home() / ".jobsearch-stage3-runs"
DEFAULT_SCHEDULE_PATH = Path.home() / ".jobsearch-stage3-schedules.json"
DEFAULT_SCHEDULE_BACKUP_PATH = Path.home() / ".jobsearch-stage3-schedules.json.bak"
DEFAULT_PLAYWRIGHT_TIMEOUT = 30.0


class ConfigError(RuntimeError):
    """Raised for a missing, unreadable or contradictory configuration.

    Carries no secret values: the message names keys, never their contents.
    """


def redact(value: str, keep: int = 4) -> str:
    """Render a secret safe to print. `123456:AAHdqTcvCH1vGWJxfSe` -> `1234…fSe`."""
    if not value:
        return "<empty>"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}…{value[-3:]}"


def parse_env_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE lines. Ignores blanks, comments and malformed lines.

    Deliberately not `dotenv`: this has to run with only the standard library plus
    python-telegram-bot, and the format the two existing env files use is this simple.
    A trailing inline comment is *not* stripped — a `#` is legal inside a password, and
    silently truncating one would produce an authentication failure with no visible
    cause.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # Strip one matched pair of surrounding quotes, nothing more.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[key] = value
    return out


def parse_user_ids(raw: str) -> list[int]:
    """Parse a comma-separated allowlist. Rejects anything non-numeric.

    A silently dropped malformed id would lock the owner out of their own bot with no
    message saying why, so this raises instead.
    """
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            raise ConfigError(
                f"STAGE3_ALLOWED_USER_IDS contains a non-numeric entry: {part!r}. "
                "Telegram user ids are integers; get yours from @userinfobot."
            )
        ids.append(int(part))
    if not ids:
        raise ConfigError(
            "STAGE3_ALLOWED_USER_IDS is empty. An open bot would let anyone start "
            "pipeline runs and read job data, so the bot refuses to start without an "
            "allowlist."
        )
    return ids


def check_permissions(path: Path, warn=None) -> None:
    """Warn if the env file is group- or world-readable.

    Not fatal: refusing to start over a permission bit would be a worse failure than
    the risk it prevents on a single-user laptop. But it is said out loud, because the
    file holds a bot token that can start runs.
    """
    if warn is None:
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & (stat.S_IRGRP | stat.S_IROTH | stat.S_IWGRP | stat.S_IWOTH):
        warn(f"{path} is readable beyond its owner (mode {stat.S_IMODE(mode):04o}); "
             f"it holds a bot token. Fix with: chmod 600 {path}")


@dataclass(frozen=True)
class Stage3Config:
    """Everything the bot needs, with no secret in its repr."""

    bot_token: str
    chat_id: int
    allowed_user_ids: tuple[int, ...]
    repo: Path = REPO_ROOT
    max_runtime: int = DEFAULT_MAX_RUNTIME
    linkedin_email: str = ""
    linkedin_password: str = ""
    run_state_root: Path = DEFAULT_RUN_STATE_ROOT
    schedule_path: Path = DEFAULT_SCHEDULE_PATH
    schedule_backup_path: Path = DEFAULT_SCHEDULE_BACKUP_PATH
    linkedin_storage_state: Path | None = None
    playwright_headless: bool = True
    playwright_timeout: float = DEFAULT_PLAYWRIGHT_TIMEOUT
    source: Path = field(default=DEFAULT_ENV_PATH)

    def __repr__(self) -> str:
        """Redacted by construction, so an accidental log or traceback is safe."""
        return (f"Stage3Config(bot_token={redact(self.bot_token)!r}, "
                f"chat_id={self.chat_id}, "
                f"allowed_user_ids={self.allowed_user_ids}, "
                f"repo={str(self.repo)!r}, max_runtime={self.max_runtime}, "
                f"linkedin_email={'set' if self.linkedin_email else 'unset'}, "
                f"linkedin_password={'set' if self.linkedin_password else 'unset'})")

    __str__ = __repr__

    def is_authorised(self, user_id) -> bool:
        """Whitelist check. Absent or malformed ids are refused, never defaulted in."""
        try:
            return int(user_id) in self.allowed_user_ids
        except (TypeError, ValueError):
            return False

    @property
    def has_linkedin_credentials(self) -> bool:
        return bool(self.linkedin_email and self.linkedin_password)

    def child_env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """The environment to hand `run_daily.sh`.

        Credentials travel here rather than in argv: argv is world-readable through
        `ps`, so `--password X` would expose the value to every process on the machine.
        The bot's own token is *removed* from the child's environment — the pipeline has
        no reason to hold it, and a subprocess that cannot read a secret cannot leak it.
        """
        env = dict(os.environ)
        env.pop("STAGE3_BOT_TOKEN", None)
        if self.linkedin_email:
            env["LINKEDIN_EMAIL"] = self.linkedin_email
        if self.linkedin_password:
            env["LINKEDIN_PASSWORD"] = self.linkedin_password
        # launchd and a bot started from a GUI both hand over a minimal PATH, and
        # `run_daily.sh` needs ~/.local/bin for tg-notify. Same fix as run_daily.sh:7.
        local_bin = str(Path.home() / ".local" / "bin")
        path = env.get("PATH", "")
        if local_bin not in path.split(":"):
            env["PATH"] = f"{local_bin}:{path}" if path else local_bin
        env.update(extra or {})
        return env


def validate_token_isolation(token: str, warn=None) -> None:
    """Refuse a token already claimed by another poller on this machine.

    Telegram gives `getUpdates` to one consumer per token. Sharing with the Claude Code
    bot costs phone access to Claude; sharing with the selector bot means a run's own
    job-selection prompt races with this bot and one of them silently loses updates.
    Both are hard to diagnose from the symptom, so they are caught at startup.
    """
    if not token:
        return
    for path, label in ((SELECTOR_ENV_PATH, "the selector bot (telegram_select.py)"),
                        (CLAUDE_BOT_ENV_PATH, "the Claude Code bot")):
        try:
            other = parse_env_file(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        for key in ("SELECTOR_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"):
            if other.get(key) and other[key] == token:
                raise ConfigError(
                    f"STAGE3_BOT_TOKEN is the same token {label} uses ({key} in "
                    f"{path}). Telegram hands getUpdates to exactly one consumer per "
                    "token, so both bots would 409 and race to steal each other's "
                    "updates. Create a separate bot with @BotFather."
                )
    if warn is not None and not SELECTOR_ENV_PATH.exists():
        warn(f"{SELECTOR_ENV_PATH} not found, so the selector bot's token could not be "
             "compared against this one. Phase 3 needs that bot to offer jobs for "
             "selection; check it is configured before relying on a full run.")


def load_config(path: Path | None = None, environ: dict | None = None,
                warn=None) -> Stage3Config:
    """Load and validate the configuration. Raises ConfigError with a fix, not a trace.

    Resolution order for each value: the env file, then the process environment. The
    file wins because it is the durable configuration; the process environment is the
    override for a one-off (`STAGE3_CHAT_ID=... python -m stage_3.bot`).
    """
    environ = os.environ if environ is None else environ
    env_path = Path(path or environ.get("JOBSEARCH_STAGE3_ENV") or DEFAULT_ENV_PATH)

    values: dict[str, str] = {}
    if env_path.exists():
        check_permissions(env_path, warn)
        try:
            values = parse_env_file(env_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read {env_path}: {exc.strerror}") from exc

    def get(key: str, default: str = "") -> str:
        return values.get(key) or environ.get(key) or default

    token = get("STAGE3_BOT_TOKEN")
    if not token:
        raise ConfigError(
            f"STAGE3_BOT_TOKEN is not set.\n"
            f"Create {env_path} with mode 600 and these keys:\n"
            "    STAGE3_BOT_TOKEN=<from @BotFather>\n"
            "    STAGE3_CHAT_ID=<your numeric id, from @userinfobot>\n"
            "    STAGE3_ALLOWED_USER_IDS=<your numeric id>\n"
            "It must be a bot of its own — not the Claude Code bot's token and not the "
            "selector's, because Telegram allows one getUpdates consumer per token."
        )
    if ":" not in token or not token.split(":", 1)[0].isdigit():
        # Caught here rather than at the first API call, which returns a bare 401 that
        # looks like a revoked token rather than a malformed one.
        raise ConfigError(
            "STAGE3_BOT_TOKEN is not shaped like a Telegram token (expected "
            f"`<digits>:<secret>`, got {redact(token)}). Copy it from @BotFather "
            "without the surrounding quotes or a trailing newline."
        )
    validate_token_isolation(token, warn)

    chat_raw = get("STAGE3_CHAT_ID")
    if not chat_raw:
        raise ConfigError(
            "STAGE3_CHAT_ID is not set — the bot would have nowhere to send an "
            "unprompted message (a finished run, a failure). Message @userinfobot to "
            "get your numeric id."
        )
    if not chat_raw.lstrip("-").isdigit():
        raise ConfigError(f"STAGE3_CHAT_ID must be numeric, got {chat_raw!r}. A "
                          "@username will not work; @userinfobot gives the id.")

    allowed_raw = get("STAGE3_ALLOWED_USER_IDS", "")
    allowed = parse_user_ids(allowed_raw) if allowed_raw.strip(" ,;") else [int(chat_raw)]

    repo = Path(get("STAGE3_REPO", str(REPO_ROOT))).expanduser()
    if not (repo / "scripts" / "run_daily.sh").is_file():
        raise ConfigError(
            f"STAGE3_REPO={repo} does not look like this repo — "
            "scripts/run_daily.sh is missing. The bot supervises that script; without "
            "it there is nothing to run."
        )

    runtime_raw = get("STAGE3_MAX_RUNTIME", str(DEFAULT_MAX_RUNTIME))
    if not runtime_raw.isdigit():
        raise ConfigError(f"STAGE3_MAX_RUNTIME must be a whole number of seconds "
                          f"(0 disables the ceiling), got {runtime_raw!r}")

    email = get("LINKEDIN_EMAIL")
    password = get("LINKEDIN_PASSWORD")
    run_state_root = Path(get("STAGE3_RUN_STATE_ROOT", str(DEFAULT_RUN_STATE_ROOT))).expanduser()
    schedule_path = Path(get("STAGE3_SCHEDULE_PATH", str(DEFAULT_SCHEDULE_PATH))).expanduser()
    schedule_backup_path = Path(
        get("STAGE3_SCHEDULE_BACKUP_PATH", str(DEFAULT_SCHEDULE_BACKUP_PATH))
    ).expanduser()
    storage_raw = get("LINKEDIN_PLAYWRIGHT_STORAGE_STATE")
    linkedin_storage_state = Path(storage_raw).expanduser() if storage_raw else None
    headless_raw = get("LINKEDIN_PLAYWRIGHT_HEADLESS", "true").lower()
    if headless_raw not in {"true", "false", "1", "0", "yes", "no"}:
        raise ConfigError("LINKEDIN_PLAYWRIGHT_HEADLESS must be true or false")
    playwright_timeout_raw = get("LINKEDIN_PLAYWRIGHT_TIMEOUT", str(DEFAULT_PLAYWRIGHT_TIMEOUT))
    try:
        playwright_timeout = float(playwright_timeout_raw)
    except ValueError as exc:
        raise ConfigError("LINKEDIN_PLAYWRIGHT_TIMEOUT must be a positive number") from exc
    if playwright_timeout <= 0 or playwright_timeout > 300:
        raise ConfigError("LINKEDIN_PLAYWRIGHT_TIMEOUT must be between 0 and 300 seconds")
    if warn is not None and bool(email) != bool(password):
        # Half-configured credentials read as "tier 3 is available" and then fail at the
        # login form, after the two cheaper tiers have already been skipped.
        missing = "LINKEDIN_PASSWORD" if email else "LINKEDIN_EMAIL"
        warn(f"{missing} is not set, so tier-3 LinkedIn enrichment stays disabled. "
             "Enrichment falls back to WebBridge and guest snippets, which is the "
             "normal path — set both keys only if you want the authenticated tier.")

    return Stage3Config(
        bot_token=token,
        chat_id=int(chat_raw),
        allowed_user_ids=tuple(allowed),
        repo=repo,
        max_runtime=int(runtime_raw),
        linkedin_email=email,
        linkedin_password=password,
        run_state_root=run_state_root,
        schedule_path=schedule_path,
        schedule_backup_path=schedule_backup_path,
        linkedin_storage_state=linkedin_storage_state,
        playwright_headless=headless_raw in {"true", "1", "yes"},
        playwright_timeout=playwright_timeout,
        source=env_path,
    )
