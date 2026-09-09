"""Load [tool.impacttest] configuration from pyproject.toml (Phase 1, spec §21)."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class Config:
    source_roots: list[str]
    test_roots: list[str]
    base_branch: str
    ignore: list[str] = field(default_factory=list)


def load_config(root: Path) -> Config:
    """Load the [tool.impacttest] table from ``root/pyproject.toml``."""
    pyproject = root / "pyproject.toml"
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    raw = data["tool"]["impacttest"]
    return Config(
        source_roots=list(raw["source_roots"]),
        test_roots=list(raw["test_roots"]),
        base_branch=raw["base_branch"],
        ignore=list(raw.get("ignore", [])),
    )
