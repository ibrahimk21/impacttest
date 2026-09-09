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
    remainder = parts[len(root_parts):]

    if not remainder or not remainder[-1].endswith(".py"):
        return None

    if remainder[-1] == "__init__.py":
        remainder = remainder[:-1]
    else:
        remainder = remainder[:-1] + [remainder[-1][: -len(".py")]]

    if not remainder:
        return None
    return ".".join(remainder)


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

    for root in config.source_roots:
        for rel in _walk_root(repo_root, root, config.ignore):
            name = path_to_module_name(rel, root)
            if name is not None:
                found[rel] = Module(path=rel, name=name)

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
                found[rel] = Module(path=rel, name=name)

    return sorted(found.values(), key=lambda m: m.path.as_posix())
