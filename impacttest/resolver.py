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


def _join(base: str, suffix: str) -> str:
    return f"{base}.{suffix}" if base else suffix


def _containing_package(importer: Module) -> str:
    """The package a module's relative imports count outward from.

    For a normal module this is its parent: ``app.payments.stripe`` sits
    in ``app.payments``. For a package initializer it is the package
    *itself* -- ``app/payments/__init__.py`` is named ``app.payments``
    and is already the package, so dropping a component would shift every
    relative import inside every ``__init__.py`` one level too far up
    (spec §24). Returns "" for a top-level module, which has no package.
    """
    if importer.path.name == "__init__.py":
        return importer.name
    return ".".join(importer.name.split(".")[:-1])


def _relative_base(importer: Module, level: int) -> str | None:
    """The package ``level`` dots point at, or None if that walks off the
    top of the source root.

    One dot is the containing package; each extra dot goes up one more.
    Landing exactly at "" is legal -- that is the source root, where
    top-level modules live. Going past it is not, and gets no guess: an
    import we cannot place is reported, never invented.
    """
    parts = [p for p in _containing_package(importer).split(".") if p]
    ascend = level - 1
    if ascend > len(parts):
        return None
    return ".".join(parts[: len(parts) - ascend])


def _known_ancestors(name: str, index: ModuleIndex) -> list[str]:
    """The proper prefixes of ``name`` that are modules in this repo,
    outermost first: ``app.sub.mod`` -> ``["app", "app.sub"]``.

    Importing a submodule imports its parents on the way in -- reaching
    ``app.sub.mod`` executes ``app/__init__.py``, then
    ``app/sub/__init__.py``. Those are real dependencies of the importer,
    so they are real edges.

    They also decide the difference between "external" and "broken".
    ``numpy.linalg`` has no prefix in the table, so the whole import
    belongs to the environment and is dropped. ``app.missing`` has
    ``app`` in the table, so the import is aimed at our own code and
    landed nowhere -- something we should not quietly ignore.
    """
    parts = name.split(".")
    prefixes = (".".join(parts[:i]) for i in range(1, len(parts)))
    return [prefix for prefix in prefixes if prefix in index]


def _resolve_target(
    target: str, names: tuple[str, ...], index: ModuleIndex
) -> tuple[str, ...]:
    """Every internal module a statement aimed at ``target`` touches.

    ``from app import users`` is ambiguous on its face: ``users`` may be
    a submodule ``app/users.py`` or an attribute defined in
    ``app/__init__.py``, and which one it is cannot be read off the
    statement. Python decides at runtime by importing ``app`` and looking
    for the attribute before falling back to the submodule; we decide the
    same question against the module table, which is the static
    equivalent of the filesystem check (spec §24).

    Both answers can be right at once, so both edges are emitted. Even
    when ``app.users`` is a real submodule, importing it runs
    ``app/__init__.py`` first -- that is how Python's import system
    works, not a guess -- so a change to the package initializer really
    can break the importer. Emitting only the deeper edge would lose
    that, and a lost edge is a test that never runs. The extra edge costs
    a denser graph around package initializers, which are rarely touched;
    the missing one costs a false negative, which is the failure this
    tool exists to prevent.

    A star import contributes nothing beyond ``target`` itself: "*" is
    not a name that could be a submodule, and module-level granularity
    means we never needed to know which names it pulled in (spec §24).
    """
    found: list[str] = _known_ancestors(target, index) if target else []
    if target and target in index:
        found.append(target)

    for name in names:
        if name == "*":
            continue
        candidate = _join(target, name)
        if candidate in index and candidate not in found:
            found.append(candidate)

    return tuple(found)


def resolve_import(
    imp: RawImport, importer: Module, index: ModuleIndex
) -> Resolution:
    """Resolve one import statement, as written in ``importer``, against
    the repository's module table.

    An absolute import is a table lookup: ``from app.users import User``
    written anywhere resolves to ``app.users`` if that name is indexed.
    The lookup is what filters out the environment: an import with no
    internal name anywhere in it -- not the full dotted path, not any
    prefix of it -- is third-party or stdlib and is dropped, edgeless and
    without complaint (spec §11). Dropping is the expected outcome for
    most imports in a real repository, not a failure.

    A relative import needs the importer's own position first: ``from
    .users import User`` means nothing in isolation, and only becomes
    ``app.users`` once you know the statement was written inside package
    ``app``. Because a relative import can only ever name something
    inside the repository, one that fails to resolve is a genuine
    unknown and is flagged, not dropped.
    """
    if imp.level:
        base = _relative_base(importer, imp.level)
        if base is None:
            return Resolution(
                uncertain=True,
                reason=(
                    f"relative import at level {imp.level} in "
                    f"'{importer.name}' reaches above the source root"
                ),
            )
        target = _join(base, imp.module) if imp.module else base
    else:
        # Only a relative import can omit the module ("from . import x"),
        # so at level 0 this is unreachable in practice; treat it as
        # external rather than crashing on a shape we did not anticipate.
        if imp.module is None:
            return Resolution()
        target = imp.module

    modules = _resolve_target(target, imp.names, index)

    # Whether the statement found what it was aiming at -- not the same
    # as having produced edges, since an unresolvable "from .missing
    # import x" still yields an edge to its package. "from .. import x"
    # names no target of its own, so there the names are the aim.
    resolved = target in index if target else bool(modules)

    if imp.level and not resolved:
        # "from . import nope" has no module of its own to name in the
        # message, so fall back to the imported names.
        sought = target or " / ".join(n for n in imp.names if n != "*")
        return Resolution(
            modules=modules,
            uncertain=True,
            reason=(
                f"relative import in '{importer.name}' does not resolve to a "
                f"known module (looked for '{sought}')"
            ),
        )

    return Resolution(modules=modules)
