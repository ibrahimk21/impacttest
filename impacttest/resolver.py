"""Resolve import statements to internal module names (Phase 3, spec §12)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from impacttest.config import Config
from impacttest.discovery import path_to_module_name, sort_roots_by_specificity
from impacttest.models import Module, RawImport


def module_name_for_path(rel_path: Path, config: Config) -> str | None:
    """Map a repo-root-relative path to the dotted module name this tool
    uses to identify it, or None if the path is outside every configured
    root (or isn't a ``.py`` file at all).

    Roots are tried most-specific-first, by the shared rule in
    :func:`impacttest.discovery.sort_roots_by_specificity`, so a path
    under both ``.`` and ``src`` gets the ``src``-relative name
    ``app.auth``. Discovery names files by that same rule, which is what
    keeps the two from disagreeing about what a file is called.

    A path under a test root but no source root is named relative to the
    repo root (``tests/test_cart.py`` -> ``tests.test_cart``), matching
    how :func:`impacttest.discovery.discover_modules` names test modules.
    Nothing imports a test module by name, so the value here is only that
    it is a stable identifier -- but it has to be the *same* stable
    identifier discovery chose, or a changed helper module under a test
    root would map to a module name no graph node has, and its dependent
    tests would silently go unselected.

    This exists for paths that are not on disk -- a deleted file named by
    ``git diff`` has no discovered Module to look up. For files that do
    exist, ``ModuleIndex.by_path`` answers the same question without
    recomputing anything.
    """
    for root in sort_roots_by_specificity(config.source_roots):
        name = path_to_module_name(rel_path, root)
        if name is not None:
            return name

    under_test_root = any(
        path_to_module_name(rel_path, root) is not None for root in config.test_roots
    )
    if under_test_root:
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
) -> tuple[tuple[str, ...], bool]:
    """Every internal module a statement aimed at ``target`` touches, and
    whether the statement actually found what it was aiming at.

    The second half of that matters because the two are not the same. An
    unresolvable ``import app.missing`` still yields an edge to ``app``;
    having produced edges is not evidence of having resolved. Anything
    the names reach counts as a hit, though -- ``from app import auth``
    finds ``app.auth`` whether or not ``app`` itself has an
    ``__init__.py``, and a package directory that ships no initializer
    has no initializer to depend on.

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
    hit = bool(target) and target in index
    if hit:
        found.append(target)

    for name in names:
        if name == "*":
            continue
        candidate = _join(target, name)
        if candidate in index:
            hit = True
            if candidate not in found:
                found.append(candidate)

    return tuple(found), hit


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

    What is *not* allowed is a guess. An import aimed at a name this
    repository partly owns but does not provide, or at a name two files
    both provide, is reported uncertain -- Phase 10 turns that into a
    full-suite run, which is expensive and correct, where a guess is
    cheap and occasionally silently wrong.

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

    modules, resolved = _resolve_target(target, imp.names, index)

    if not resolved and imp.level:
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

    if not resolved and modules:
        # Absolute, and part of the name is ours: "import app.missing"
        # where app is a package of ours but nothing provides
        # app/missing.py. A dotted name in an import statement has to be
        # a module, so this is not the harmless attribute case -- it is a
        # module we failed to find, and the edges we do have may be
        # incomplete.
        return Resolution(
            modules=modules,
            uncertain=True,
            reason=(
                f"'{target}' is imported by '{importer.name}' and looks "
                "internal, but no file in this repository provides it"
            ),
        )

    contested = [name for name in modules if name in index.ambiguous]
    if contested:
        return Resolution(
            modules=modules,
            uncertain=True,
            reason=(
                f"'{contested[0]}' is provided by more than one file, so the "
                "graph cannot tell which one this import means"
            ),
        )

    return Resolution(modules=modules)


@dataclass(frozen=True)
class ResolvedModule:
    """One module with its internal dependencies resolved."""

    name: str
    path: Path
    is_test: bool = False
    depends_on: tuple[str, ...] = ()
    uncertain: bool = False
    reasons: tuple[str, ...] = ()


def resolve_module(module: Module, index: ModuleIndex) -> ResolvedModule:
    """Resolve every import in one module into a dependency set.

    Uncertainty accumulates rather than being decided per statement: the
    module is uncertain if any of its imports could not be resolved, or
    if the analyzer already flagged it for a dynamic-import construct in
    Phase 2. Both kinds mean the same thing downstream -- this module's
    dependency list may be incomplete -- and both carry a reason string
    so the fallback can say why it fired (spec §19).

    Self-dependencies are dropped. A package initializer that does "from
    . import thing" resolves partly to its own package, which is true and
    useless: it would add a self-loop to every such node and a pointless
    hop to every explanation path.
    """
    depends: set[str] = set()
    reasons: list[str] = []
    uncertain = module.uncertain

    if module.uncertain:
        reasons.append(
            f"'{module.name}' uses a dynamic import construct, so its "
            "dependencies cannot be read statically"
        )

    for imp in module.imports:
        resolution = resolve_import(imp, module, index)
        depends.update(resolution.modules)
        if resolution.uncertain:
            uncertain = True
            if resolution.reason and resolution.reason not in reasons:
                reasons.append(resolution.reason)

    depends.discard(module.name)

    return ResolvedModule(
        name=module.name,
        path=module.path,
        is_test=module.is_test,
        depends_on=tuple(sorted(depends)),
        uncertain=uncertain,
        reasons=tuple(reasons),
    )


def resolve_all(
    modules: Iterable[Module], index: ModuleIndex
) -> list[ResolvedModule]:
    """Resolve a whole repository, in the order given."""
    return [resolve_module(module, index) for module in modules]
