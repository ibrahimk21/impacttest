"""Core dataclasses: Module, ChangeSet, Selection (Phase 1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Module:
    """A single Python file discovered in the repository."""

    path: Path
    name: str
    is_test: bool = False
    imports: list[str] = field(default_factory=list)
    uncertain: bool = False


@dataclass
class ChangeSet:
    """Files changed between a base revision and HEAD (Phase 6, spec §10)."""

    added: list[Path] = field(default_factory=list)
    modified: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)
    renamed: list[tuple[Path, Path]] = field(default_factory=list)


@dataclass
class Selection:
    """The result of impact analysis: which tests to run, and why (Phase 5/10)."""

    tests: list[Path] = field(default_factory=list)
    fallback: bool = False
    fallback_reason: str | None = None
