#!/usr/bin/env python3
"""Write the selector handoff as valid JSON without shell interpolation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def write_handoff(
    path: Path,
    *,
    today: str,
    rankset: Path,
    run_id: str = "",
    output_root: Path | None = None,
) -> None:
    """Atomically write required fields plus nonempty Stage 3 fields."""
    path = Path(path)
    payload = {"today": today, "rankset": str(rankset)}
    if run_id:
        payload["run_id"] = run_id
    if output_root is not None and str(output_root):
        payload["output_root"] = str(output_root)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 5:
        raise SystemExit(
            "usage: write_selection_handoff.py PATH TODAY RANKSET RUN_ID OUTPUT_ROOT"
        )
    path, today, rankset, run_id, output_root = args
    write_handoff(
        Path(path),
        today=today,
        rankset=Path(rankset),
        run_id=run_id,
        output_root=Path(output_root) if output_root else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
