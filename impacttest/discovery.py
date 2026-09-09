"""Repository root, source/test file discovery, test-file classification (Phase 1)."""
from __future__ import annotations

import subprocess
from pathlib import Path


def find_repo_root(start: Path | None = None) -> Path:
    """Locate the Git repository root via ``git rev-parse --show-toplevel``."""
    cwd = start or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise RuntimeError(
            f"{cwd} is not inside a Git repository (or git is not installed)"
        ) from exc
    return Path(result.stdout.strip()).resolve()
