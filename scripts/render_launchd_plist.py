#!/usr/bin/env python3
"""Render launchd plist templates for the checkout invoking the installer."""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path
from xml.sax.saxutils import escape


def render(template: Path, destination: Path, *, repo: Path, python: Path) -> None:
    text = template.read_text(encoding="utf-8")
    replacements = {
        "__PROJECT_DIR__": escape(str(repo)),
        "__PYTHON__": escape(str(python)),
        "__HOME__": escape(str(Path.home())),
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    payload = plistlib.loads(text.encode("utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("wb") as stream:
            plistlib.dump(payload, stream, sort_keys=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4:
        raise SystemExit("usage: render_launchd_plist.py TEMPLATE DEST REPO PYTHON")
    template, destination, repo, python = map(Path, args)
    repo = repo.resolve(strict=True)
    python = python.resolve(strict=True)
    if not (repo / "scripts" / "run_daily.sh").is_file():
        raise SystemExit(f"not a job-search checkout: {repo}")
    render(template, destination, repo=repo, python=python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
