from pathlib import Path

from impacttest.graph import build_graph
from impacttest.impact import compute_impact
from impacttest.resolver import ResolvedModule


def _resolved(name: str, *, depends_on=(), is_test=False) -> ResolvedModule:
    return ResolvedModule(
        name=name,
        path=Path(f"{name.replace('.', '/')}.py"),
        is_test=is_test,
        depends_on=tuple(depends_on),
    )


def test_direct_dependency_is_selected():
    # tests.test_crypto -> app.crypto (spec §23 Req 2)
    modules = [
        _resolved("tests.test_crypto", depends_on=["app.crypto"], is_test=True),
        _resolved("app.crypto"),
    ]
    graph = build_graph(modules)

    result = compute_impact(["app.crypto"], graph, modules)

    assert result.tests == ["tests.test_crypto"]
    assert result.impacted == {"app.crypto", "tests.test_crypto"}
    assert result.parents == {"tests.test_crypto": "app.crypto"}


def test_transitive_chain_is_selected():
    # tests.test_login -> app.login -> app.tokens -> app.crypto (spec §23 Req 3)
    modules = [
        _resolved("tests.test_login", depends_on=["app.login"], is_test=True),
        _resolved("app.login", depends_on=["app.tokens"]),
        _resolved("app.tokens", depends_on=["app.crypto"]),
        _resolved("app.crypto"),
    ]
    graph = build_graph(modules)

    result = compute_impact(["app.crypto"], graph, modules)

    assert result.tests == ["tests.test_login"]
    assert result.impacted == {
        "app.crypto",
        "app.tokens",
        "app.login",
        "tests.test_login",
    }
    # The parent chain walks back from the test to the changed module.
    assert result.parents["tests.test_login"] == "app.login"
    assert result.parents["app.login"] == "app.tokens"
    assert result.parents["app.tokens"] == "app.crypto"


def test_unrelated_tests_are_not_selected():
    # spec §23 Req 4: only tests reachable from the change are selected.
    modules = [
        _resolved("tests.test_crypto", depends_on=["app.crypto"], is_test=True),
        _resolved("app.crypto"),
        _resolved("tests.test_unrelated", depends_on=["app.unrelated"], is_test=True),
        _resolved("app.unrelated"),
    ]
    graph = build_graph(modules)

    result = compute_impact(["app.crypto"], graph, modules)

    assert result.tests == ["tests.test_crypto"]
    assert "tests.test_unrelated" not in result.impacted


def test_cycle_terminates_and_selects_all_tests_in_it():
    # app.a -> app.b -> app.a, with a test depending on each (spec §23 Req 5).
    modules = [
        _resolved("app.a", depends_on=["app.b"]),
        _resolved("app.b", depends_on=["app.a"]),
        _resolved("tests.test_a", depends_on=["app.a"], is_test=True),
        _resolved("tests.test_b", depends_on=["app.b"], is_test=True),
    ]
    graph = build_graph(modules)

    result = compute_impact(["app.a"], graph, modules)

    assert result.tests == ["tests.test_a", "tests.test_b"]
    assert result.impacted == {"app.a", "app.b", "tests.test_a", "tests.test_b"}


def test_changed_test_file_is_selected_even_with_no_dependents():
    # spec §23 Req 1: a changed test file is always selected, dependents
    # or not.
    modules = [_resolved("tests.test_isolated", is_test=True)]
    graph = build_graph(modules)

    result = compute_impact(["tests.test_isolated"], graph, modules)

    assert result.tests == ["tests.test_isolated"]
    assert result.impacted == {"tests.test_isolated"}
    assert result.parents == {}


def test_changed_non_test_module_with_no_dependents_selects_nothing():
    modules = [_resolved("app.orphan")]
    graph = build_graph(modules)

    result = compute_impact(["app.orphan"], graph, modules)

    assert result.tests == []
    assert result.impacted == {"app.orphan"}
