"""Repository root, source/test file discovery, test-file classification (Phase 1)."""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path, PurePosixPath

from impacttest.config import Config
from impacttest.models import Module


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


def path_to_module_name(rel_path: Path, root: str) -> str | None:
    """Map a repo-root-relative path to a dotted module name under ``root``.

    ``root == "." (or "")`` means "the repo root itself" -- nothing is
    stripped. Returns None if ``rel_path`` isn't under ``root``, isn't a
    ``.py`` file, or resolves to an empty name (e.g. a bare ``__init__.py``
    sitting directly in ``root`` with nothing above it to name the package).
    """
    parts = list(PurePosixPath(rel_path.as_posix()).parts)
    root_parts = [] if root in ("", ".") else list(PurePosixPath(root).parts)

    if parts[: len(root_parts)] != root_parts:
        return None
    remainder = parts[len(root_parts) :]

    if not remainder or not remainder[-1].endswith(".py"):
        return None

    if remainder[-1] == "__init__.py":
        remainder = remainder[:-1]
    else:
        remainder = remainder[:-1] + [remainder[-1][: -len(".py")]]

    if not remainder:
        return None
    return ".".join(remainder)


def _root_depth(root: str) -> int:
    """How specific a configured root is, in path components."""
    if root in ("", "."):
        return 0
    return len(PurePosixPath(root).parts)


def sort_roots_by_specificity(roots: list[str]) -> list[str]:
    """Configured roots, most specific first.

    Roots can nest -- ``["src", "."]`` is legal, and ``src/app/auth.py``
    lies under both. Whoever names that file has to make the same choice
    every time, or discovery and the resolver end up calling one file two
    different things and the import connecting them resolves to nothing.
    So the rule lives here, once, and both callers take it from here.

    Most specific wins because that is the root a src layout actually
    puts on ``sys.path``: other modules import the file as ``app.auth``,
    never as ``src.app.auth``, and a name only produces an edge if both
    ends spell it identically.
    """
    return sorted(roots, key=_root_depth, reverse=True)


def is_test_file(rel_path: Path, test_roots: list[str]) -> bool:
    """A file is a test file if its name matches the pytest convention
    AND it lives under one of the configured test roots -- a helper
    module named test_utils.py sitting in source is not a test suite.
    """
    name = rel_path.name
    looks_like_test = name.startswith("test_") or name.endswith("_test.py")
    if not looks_like_test:
        return False
    return any(_is_under(rel_path, root) for root in test_roots)


def _is_under(rel_path: Path, root: str) -> bool:
    if root in ("", "."):
        return True
    return PurePosixPath(rel_path.as_posix()).is_relative_to(PurePosixPath(root))


def _is_ignored(rel_path: Path, patterns: list[str]) -> bool:
    posix = rel_path.as_posix()
    return any(fnmatch.fnmatch(posix, pattern) for pattern in patterns)


def _walk_root(repo_root: Path, root: str, ignore: list[str]) -> list[Path]:
    root_dir = repo_root if root in ("", ".") else repo_root / root
    if not root_dir.is_dir():
        return []

    found = []
    for path in sorted(root_dir.rglob("*.py")):
        rel = path.relative_to(repo_root)
        if not _is_ignored(rel, ignore):
            found.append(rel)
    return found


def discover_modules(repo_root: Path, config: Config) -> list[Module]:
    """Enumerate every Python file under the configured source and test
    roots, mapping each to a Module with its dotted name computed.
    """
    found: dict[Path, Module] = {}

    # Most specific root first, and first match wins, so a file under two
    # nested roots is named by the deeper one -- the same choice
    # resolver.module_name_for_path makes for paths that no longer exist.
    for root in sort_roots_by_specificity(config.source_roots):
        for rel in _walk_root(repo_root, root, config.ignore):
            if rel in found:
                continue
            name = path_to_module_name(rel, root)
            if name is not None:
                found[rel] = Module(
                    path=rel,
                    name=name,
                    is_test=is_test_file(rel, config.test_roots),
                )

    for root in config.test_roots:
        for rel in _walk_root(repo_root, root, config.ignore):
            if rel in found:
                continue
            # Test module names keep the full repo-relative path (e.g.
            # "tests.test_cart") rather than stripping the test root --
            # nothing ever imports a test module by name, so this is just
            # a stable, readable internal identifier.
            name = path_to_module_name(rel, ".")
            if name is not None:
                found[rel] = Module(
                    path=rel,
                    name=name,
                    is_test=is_test_file(rel, config.test_roots),
                )

    return sorted(found.values(), key=lambda m: m.path.as_posix())
