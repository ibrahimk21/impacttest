"""Core dataclasses: Module, ChangeSet, Selection (Phase 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RawImport:
    """One import statement as extracted from source, before resolution.

    ``module`` is the dotted name written in the statement (None only for
    a bare ``from . import x``, where nothing follows the dots). ``level``
    is the relative-import dot count: 0 for an absolute import. ``names``
    are the imported names, or ("*",) for a star import -- not used for
    module-level dependency resolution, kept for explainability.
    """

    module: str | None
    level: int
    names: tuple[str, ...] = ()


@dataclass
class Module:
    """A single Python file discovered in the repository."""

    path: Path
    name: str
    is_test: bool = False
    imports: list[RawImport] = field(default_factory=list)
    uncertain: bool = False


@dataclass
class ChangeSet:
    """Files changed between a base revision and HEAD (Phase 6, spec §10).

    ``added``/``modified``/``deleted``/``renamed`` hold only ``.py`` paths --
    the ones that feed the resolver and dependency graph. Everything else
    that changed (``pyproject.toml``, ``pytest.ini``, and the like) goes into
    ``non_python`` instead: it can never produce a graph edge, but Phase 10's
    global-file fallback still needs to know it changed.
    """

    added: list[Path] = field(default_factory=list)
    modified: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)
    renamed: list[tuple[Path, Path]] = field(default_factory=list)
    non_python: list[Path] = field(default_factory=list)


@dataclass
class Selection:
    """The result of impact analysis: which tests to run, and why (Phase 5/10)."""

    tests: list[Path] = field(default_factory=list)
    fallback: bool = False
    fallback_reason: str | None = None
