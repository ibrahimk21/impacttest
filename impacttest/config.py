"""Load [tool.impacttest] configuration from pyproject.toml (Phase 1, spec §21)."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


DEFAULT_SOURCE_ROOTS = ["src"]
DEFAULT_TEST_ROOTS = ["tests"]
DEFAULT_BASE_BRANCH = "main"
DEFAULT_IGNORE = [".venv/**", "venv/**", "build/**", "dist/**"]


@dataclass
class Config:
    source_roots: list[str] = field(default_factory=lambda: list(DEFAULT_SOURCE_ROOTS))
    test_roots: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_ROOTS))
    base_branch: str = DEFAULT_BASE_BRANCH
    ignore: list[str] = field(default_factory=lambda: list(DEFAULT_IGNORE))


def load_config(root: Path) -> Config:
    """Load the [tool.impacttest] table from ``root/pyproject.toml``.

    A missing file, a missing [tool.impacttest] table, or individual
    missing keys all fall back to defaults rather than raising -- a
    project with no ImpactTest config at all is a normal, supported
    case (spec §21), not an error.
    """
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        return Config()

    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    raw = data.get("tool", {}).get("impacttest", {})

    return Config(
        source_roots=list(raw.get("source_roots", DEFAULT_SOURCE_ROOTS)),
        test_roots=list(raw.get("test_roots", DEFAULT_TEST_ROOTS)),
        base_branch=raw.get("base_branch", DEFAULT_BASE_BRANCH),
        ignore=list(raw.get("ignore", DEFAULT_IGNORE)),
    )
