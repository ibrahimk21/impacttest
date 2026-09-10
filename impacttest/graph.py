"""Forward and reverse dependency graph construction (Phase 4, spec §13)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from impacttest.resolver import ResolvedModule


@dataclass
class Graph:
    """The module dependency graph, held as both adjacency directions.

    ``forward[A]`` is the set of modules ``A`` depends on (spec §13:
    ``A -> B`` means A imports B). ``reverse[B]`` is the set of modules
    that depend on B, built once by inverting every forward edge rather
    than walked at query time -- impact traversal (Phase 5) needs plain
    dict lookups to BFS from a changed module.

    Every discovered module gets a node in both directions, including one
    with no edges either way, so looking up any known module name never
    raises a ``KeyError`` -- callers do not need to special-case isolated
    modules.
    """

    forward: dict[str, set[str]] = field(default_factory=dict)
    reverse: dict[str, set[str]] = field(default_factory=dict)

    @property
    def node_count(self) -> int:
        return len(self.forward)

    @property
    def edge_count(self) -> int:
        return sum(len(deps) for deps in self.forward.values())


def build_graph(modules: Iterable[ResolvedModule]) -> Graph:
    """Build the forward graph from resolved modules, then invert it.

    An edge is only added between two names both present in ``modules``.
    Every name in ``ResolvedModule.depends_on`` was resolved against the
    same module index that produced these nodes, so in practice this
    filter never rejects anything -- it exists so a stale or partial
    ``modules`` iterable produces a smaller graph rather than a
    ``reverse`` entry for a node that was never added.

    Cycles need no special handling here: an edge is just a dict entry,
    so ``A -> B -> A`` is two entries, not a loop the builder walks.
    """
    resolved = list(modules)
    names = {module.name for module in resolved}

    graph = Graph(
        forward={module.name: set() for module in resolved},
        reverse={module.name: set() for module in resolved},
    )

    for module in resolved:
        for dependency in module.depends_on:
            if dependency not in names:
                continue
            graph.forward[module.name].add(dependency)
            graph.reverse[dependency].add(module.name)

    return graph
