from pathlib import Path

import pytest

from stage_3.config import SECRET_KEYS, ConfigError, load_config


def write_repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "run_daily.sh").write_text("#!/bin/sh\n")
    return tmp_path


def test_loader_redacts_secrets_and_removes_bot_token_from_child_env(tmp_path):
    repo = write_repo(tmp_path / "repo")
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        "STAGE3_ALLOWED_USER_IDS=42\n"
        f"STAGE3_REPO={repo}\n"
    )
    cfg = load_config(env, environ={}, warn=lambda _: None)
    assert "synthetic-secret" not in repr(cfg)
    assert "STAGE3_BOT_TOKEN" not in cfg.child_env()


def test_legacy_linkedin_credentials_are_not_loaded_or_propagated(tmp_path, monkeypatch):
    repo = write_repo(tmp_path / "repo")
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        f"STAGE3_REPO={repo}\n"
        "LINKEDIN_EMAIL=synthetic@example.com\n"
        "LINKEDIN_PASSWORD=synthetic-password\n"
    )
    monkeypatch.setenv("LINKEDIN_EMAIL", "ambient@example.com")
    monkeypatch.setenv("LINKEDIN_PASSWORD", "ambient-password")

    cfg = load_config(env, environ={}, warn=lambda _: None)
    child = cfg.child_env()

    assert not hasattr(cfg, "linkedin_email")
    assert not hasattr(cfg, "linkedin_password")
    assert "LINKEDIN_EMAIL" not in child
    assert "LINKEDIN_PASSWORD" not in child
    assert "LINKEDIN_PASSWORD" in SECRET_KEYS


def test_storage_state_is_propagated_but_legacy_credentials_cannot_be_reintroduced(
    tmp_path, monkeypatch
):
    repo = write_repo(tmp_path / "repo")
    storage = tmp_path / "state.json"
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        f"STAGE3_REPO={repo}\n"
        f"LINKEDIN_PLAYWRIGHT_STORAGE_STATE={storage}\n"
    )
    monkeypatch.delenv("LINKEDIN_PLAYWRIGHT_STORAGE_STATE", raising=False)

    cfg = load_config(env, environ={}, warn=lambda _: None)
    child = cfg.child_env({
        "LINKEDIN_EMAIL": "override@example.com",
        "LINKEDIN_PASSWORD": "override-password",
    })

    assert child["LINKEDIN_PLAYWRIGHT_STORAGE_STATE"] == str(storage)
    assert "LINKEDIN_EMAIL" not in child
    assert "LINKEDIN_PASSWORD" not in child


def test_blank_allowlist_defaults_to_chat_owner(tmp_path):
    repo = write_repo(tmp_path / "repo")
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        "STAGE3_ALLOWED_USER_IDS= , , \n"
        f"STAGE3_REPO={repo}\n"
    )
    cfg = load_config(env, environ={}, warn=lambda _: None)
    assert cfg.allowed_user_ids == (42,)


def test_config_exposes_safe_run_schedule_and_playwright_defaults(tmp_path):
    repo = write_repo(tmp_path / "repo")
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        f"STAGE3_REPO={repo}\n"
    )
    cfg = load_config(env, environ={}, warn=lambda _: None)
    assert cfg.run_state_root == Path.home() / ".jobsearch-stage3-runs"
    assert cfg.schedule_path == Path.home() / ".jobsearch-stage3-schedules.json"
    assert cfg.schedule_backup_path == Path.home() / ".jobsearch-stage3-schedules.json.bak"
    assert cfg.linkedin_storage_state is None
    assert cfg.playwright_headless is True
    assert cfg.playwright_timeout == 30.0


def test_config_reads_operational_overrides_without_repr_secrets(tmp_path):
    repo = write_repo(tmp_path / "repo")
    state = tmp_path / "runs"
    schedules = tmp_path / "schedules.json"
    storage = tmp_path / "state.json"
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        f"STAGE3_REPO={repo}\n"
        f"STAGE3_RUN_STATE_ROOT={state}\n"
        f"STAGE3_SCHEDULE_PATH={schedules}\n"
        f"STAGE3_SCHEDULE_BACKUP_PATH={schedules}.bak\n"
        f"LINKEDIN_PLAYWRIGHT_STORAGE_STATE={storage}\n"
        "LINKEDIN_PLAYWRIGHT_HEADLESS=false\n"
        "LINKEDIN_PLAYWRIGHT_TIMEOUT=45.5\n"
    )
    cfg = load_config(env, environ={}, warn=lambda _: None)
    assert cfg.run_state_root == state
    assert cfg.schedule_path == schedules
    assert cfg.schedule_backup_path == schedules.with_name("schedules.json.bak")
    assert cfg.linkedin_storage_state == storage
    assert cfg.playwright_headless is False
    assert cfg.playwright_timeout == 45.5
    assert "synthetic-secret" not in str(cfg)


def test_invalid_operational_values_raise_config_error(tmp_path):
    repo = write_repo(tmp_path / "repo")
    env = tmp_path / "stage3.env"
    env.write_text(
        "STAGE3_BOT_TOKEN=123456:synthetic-secret\n"
        "STAGE3_CHAT_ID=42\n"
        f"STAGE3_REPO={repo}\n"
        "LINKEDIN_PLAYWRIGHT_TIMEOUT=0\n"
    )
    with pytest.raises(ConfigError, match="LINKEDIN_PLAYWRIGHT_TIMEOUT"):
        load_config(env, environ={}, warn=lambda _: None)
