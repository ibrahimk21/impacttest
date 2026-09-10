"""Reverse-graph BFS impact traversal (Phase 5, spec §14)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field

from impacttest.graph import Graph
from impacttest.resolver import ResolvedModule


@dataclass
class ImpactResult:
    """Everything reached from a changed-module set, and how it was reached.

    ``impacted`` is the changed modules plus every module that transitively
    depends on one of them -- the traversal never visits anything else
    (spec §23 Req 4). ``parents`` records, for each module reached *through*
    the traversal, the module one hop closer to the change; Phase 9 walks
    it backward from a test to rebuild the ``test -> ... -> changed module``
    explanation path (spec §19). A changed module has no entry in
    ``parents`` -- that absence is what marks the walk's end. ``tests`` is
    ``impacted`` filtered down to test modules, sorted for a stable order.
    """

    impacted: set[str] = field(default_factory=set)
    parents: dict[str, str] = field(default_factory=dict)
    tests: list[str] = field(default_factory=list)


def compute_impact(
    changed_modules: Iterable[str],
    graph: Graph,
    modules: Iterable[ResolvedModule],
) -> ImpactResult:
    """BFS the reverse dependency graph from every changed module.

    Every changed module goes into ``impacted`` up front, whether or not it
    has dependents -- this is what selects a changed test file even when
    nothing imports it (spec §23 Req 1). From there, each step follows
    ``graph.reverse`` to the modules that depend on the current one, i.e.
    the modules one hop *closer* to a test and farther from the change.

    A module already in ``impacted`` is never re-queued, so a cycle in the
    dependency graph (spec §23 Req 5) is walked at most once per node
    instead of looping forever. ``graph.reverse`` is read with ``.get``
    rather than indexed, so a changed module that is not a graph node at
    all -- a deleted file, or one this ``graph`` build never saw -- still
    seeds the impacted set without a ``KeyError``; it simply has no
    dependents to add.
    """
    impacted: set[str] = set(changed_modules)
    parents: dict[str, str] = {}
    queue: deque[str] = deque(impacted)

    while queue:
        current = queue.popleft()
        for dependent in graph.reverse.get(current, ()):
            if dependent not in impacted:
                impacted.add(dependent)
                parents[dependent] = current
                queue.append(dependent)

    is_test = {module.name: module.is_test for module in modules}
    tests = sorted(name for name in impacted if is_test.get(name, False))

    return ImpactResult(impacted=impacted, parents=parents, tests=tests)
