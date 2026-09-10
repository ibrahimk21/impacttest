from pathlib import Path

from impacttest.graph import build_graph
from impacttest.resolver import ResolvedModule


def _resolved(name: str, *, depends_on=(), is_test=False) -> ResolvedModule:
    return ResolvedModule(
        name=name,
        path=Path(f"{name.replace('.', '/')}.py"),
        is_test=is_test,
        depends_on=tuple(depends_on),
    )


def test_forward_and_reverse_agree_on_a_fixture_project():
    # tests.test_login -> app.login -> app.tokens -> app.crypto (spec §13)
    modules = [
        _resolved("tests.test_login", depends_on=["app.login"], is_test=True),
        _resolved("app.login", depends_on=["app.tokens"]),
        _resolved("app.tokens", depends_on=["app.crypto"]),
        _resolved("app.crypto"),
    ]

    graph = build_graph(modules)

    assert graph.forward == {
        "tests.test_login": {"app.login"},
        "app.login": {"app.tokens"},
        "app.tokens": {"app.crypto"},
        "app.crypto": set(),
    }
    assert graph.reverse == {
        "tests.test_login": set(),
        "app.login": {"tests.test_login"},
        "app.tokens": {"app.login"},
        "app.crypto": {"app.tokens"},
    }

    # Every forward edge A -> B has a matching reverse edge B -> A, and
    # nothing else -- the two directions describe the same graph.
    for source, targets in graph.forward.items():
        for target in targets:
            assert source in graph.reverse[target]
    for target, sources in graph.reverse.items():
        for source in sources:
            assert target in graph.forward[source]

    assert graph.node_count == 4
    assert graph.edge_count == 3


def test_isolated_module_has_empty_edges_in_both_directions():
    graph = build_graph([_resolved("app.standalone")])

    assert graph.forward == {"app.standalone": set()}
    assert graph.reverse == {"app.standalone": set()}
    assert graph.node_count == 1
    assert graph.edge_count == 0


def test_cyclic_fixture_builds_without_error():
    modules = [
        _resolved("app.a", depends_on=["app.b"]),
        _resolved("app.b", depends_on=["app.a"]),
    ]

    graph = build_graph(modules)

    assert graph.forward == {"app.a": {"app.b"}, "app.b": {"app.a"}}
    assert graph.reverse == {"app.a": {"app.b"}, "app.b": {"app.a"}}
    assert graph.node_count == 2
    assert graph.edge_count == 2


def test_dependency_outside_the_given_modules_is_dropped_defensively():
    # depends_on names a module not present in this build_graph call --
    # e.g. a stale/partial resolved-module list. The edge must not create
    # a reverse entry for a node that was never added as a source node.
    graph = build_graph([_resolved("app.a", depends_on=["app.missing"])])

    assert graph.forward == {"app.a": set()}
    assert graph.reverse == {"app.a": set()}
