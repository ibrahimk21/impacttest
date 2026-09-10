"""Resolve import statements to internal module names (Phase 3, spec §12)."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

from impacttest.config import Config
from impacttest.discovery import path_to_module_name


def _root_depth(root: str) -> int:
    """How specific a configured root is, in path components."""
    if root in ("", "."):
        return 0
    return len(PurePosixPath(root).parts)


def module_name_for_path(rel_path: Path, config: Config) -> str | None:
    """Map a repo-root-relative path to the dotted module name this tool
    uses to identify it, or None if the path is outside every configured
    root (or isn't a ``.py`` file at all).

    Roots are tried most-specific-first, so a path under both ``.`` and
    ``src`` gets the ``src``-relative name ``app.auth`` rather than
    ``src.app.auth``. That matters because the specific root is the one
    that is actually on ``sys.path`` in a src layout, which makes
    ``app.auth`` the name other modules will import it by -- and names
    only produce graph edges if both sides spell them the same way.

    A path under a test root but no source root is named relative to the
    repo root (``tests/test_cart.py`` -> ``tests.test_cart``), matching
    how :func:`impacttest.discovery.discover_modules` names test modules.
    Nothing imports a test module by name, so the value here is only that
    it is a stable identifier -- but it has to be the *same* stable
    identifier discovery chose, or a changed helper module under a test
    root would map to a module name no graph node has, and its dependent
    tests would silently go unselected.

    Discovery is still the authority for files that exist: prefer
    ``ModuleIndex.by_path`` when the file is on disk, and use this for
    paths that aren't (a deleted file named by ``git diff`` has no
    discovered Module to look up).
    """
    for root in sorted(config.source_roots, key=_root_depth, reverse=True):
        name = path_to_module_name(rel_path, root)
        if name is not None:
            return name

    if any(path_to_module_name(rel_path, root) is not None for root in config.test_roots):
        return path_to_module_name(rel_path, ".")

    return None
