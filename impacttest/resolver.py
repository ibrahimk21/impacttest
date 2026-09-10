"""Resolve import statements to internal module names (Phase 3, spec §12)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from impacttest.config import Config
from impacttest.discovery import path_to_module_name
from impacttest.models import Module, RawImport


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


@dataclass
class ModuleIndex:
    """The lookup that decides what "internal" means.

    ``by_name`` maps every module name discovered in the repository to
    its file, including packages: ``src/app/__init__.py`` is indexed
    under ``app``, so ``from app import User`` has something to resolve
    to even when ``User`` is only a name re-exported by the package
    initializer (spec §24).

    Membership in ``by_name`` is the entire definition of an internal
    module. An import that lands in here becomes a graph edge; one that
    does not is third-party or stdlib and gets dropped (spec §11). There
    is no separate list of external packages to maintain, and no attempt
    to consult the installed environment -- what is in the repository is
    what counts.

    ``ambiguous`` holds names that two or more files both claim (two
    source roots each containing an ``app/auth.py``, say). The graph has
    one node per name, so those files share a node; resolving to such a
    name still produces the edge, but is reported as uncertain rather
    than silently picking a winner.
    """

    by_name: dict[str, Path] = field(default_factory=dict)
    by_path: dict[Path, str] = field(default_factory=dict)
    ambiguous: set[str] = field(default_factory=set)

    def __contains__(self, name: object) -> bool:
        return name in self.by_name


def build_index(modules: Iterable[Module]) -> ModuleIndex:
    """Index discovered modules by name and by path.

    When two files claim one name the lower path wins the ``by_name``
    slot -- an arbitrary but deterministic choice, so that two runs over
    an unchanged repository never disagree about the graph. The name is
    recorded in ``ambiguous`` so the resolver can flag it instead of
    trusting the coin flip.
    """
    index = ModuleIndex()
    for module in sorted(modules, key=lambda m: m.path.as_posix()):
        index.by_path[module.path] = module.name
        if module.name in index.by_name:
            index.ambiguous.add(module.name)
            continue
        index.by_name[module.name] = module.path
    return index


@dataclass(frozen=True)
class Resolution:
    """What one import statement resolved to.

    ``modules`` are the internal module names the statement depends on --
    empty when the import is external, which is the common case and not
    an error. ``uncertain`` means the statement looks internal but could
    not be resolved confidently; Phase 10 escalates that to a full-suite
    run, so it is a safe answer rather than a wrong one, but an expensive
    one to hand out carelessly.
    """

    modules: tuple[str, ...] = ()
    uncertain: bool = False
    reason: str | None = None


def resolve_import(
    imp: RawImport, importer: Module, index: ModuleIndex
) -> Resolution:
    """Resolve one import statement, as written in ``importer``, against
    the repository's module table.

    An absolute import is a table lookup: ``from app.users import User``
    written anywhere resolves to ``app.users`` if that name is indexed.
    The lookup is what filters out the environment -- ``import numpy``
    finds nothing internal and contributes no edge (spec §11).
    """
    if imp.level:
        return Resolution(
            uncertain=True, reason="relative imports are not resolved yet"
        )

    # Only a relative import can omit the module ("from . import x"), so
    # at level 0 this is unreachable in practice; treat it as external
    # rather than crashing on a shape we did not anticipate.
    if imp.module is None:
        return Resolution()

    if imp.module in index:
        return Resolution(modules=(imp.module,))
    return Resolution()
