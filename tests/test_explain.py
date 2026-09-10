from pathlib import Path

from impacttest.explain import Explanation, explain_selection, format_explanation
from impacttest.impact import ImpactResult

# --- explain_selection: pure logic over an ImpactResult ---


def _paths(*names: str) -> dict[str, Path]:
    return {name: Path(f"{name.replace('.', '/')}.py") for name in names}


def test_not_found_when_path_names_no_known_module():
    impact = ImpactResult(impacted={"tests.test_a"})
    result = explain_selection(
        Path("tests/test_missing.py"),
        "tests.test_missing",
        impact,
        _paths("tests.test_a"),
    )
    assert result.found is False
    assert result.selected is False


def test_not_selected_when_known_but_not_impacted():
    impact = ImpactResult(impacted={"app.other"})
    path_by_name = _paths("tests.test_a", "app.other")
    result = explain_selection(
        Path("tests/test_a.py"), "tests.test_a", impact, path_by_name
    )
    assert result.found is True
    assert result.selected is False


def test_self_changed_when_test_is_the_change_itself():
    impact = ImpactResult(impacted={"tests.test_a"})
    path_by_name = _paths("tests.test_a")
    result = explain_selection(
        Path("tests/test_a.py"), "tests.test_a", impact, path_by_name
    )
    assert result.selected is True
    assert result.self_changed is True
    assert result.path == [Path("tests/test_a.py")]
    assert result.changed_path == Path("tests/test_a.py")


def test_three_hop_chain_is_reconstructed_in_order():
    # tests.test_login -> app.login -> app.tokens -> app.crypto (changed)
    impact = ImpactResult(
        impacted={"tests.test_login", "app.login", "app.tokens", "app.crypto"},
        parents={
            "tests.test_login": "app.login",
            "app.login": "app.tokens",
            "app.tokens": "app.crypto",
        },
    )
    path_by_name = _paths("tests.test_login", "app.login", "app.tokens", "app.crypto")

    result = explain_selection(
        Path("tests/test_login.py"), "tests.test_login", impact, path_by_name
    )

    assert result.selected is True
    assert result.self_changed is False
    assert result.path == [
        Path("tests/test_login.py"),
        Path("app/login.py"),
        Path("app/tokens.py"),
        Path("app/crypto.py"),
    ]
    assert result.changed_path == Path("app/crypto.py")


def test_chain_falls_back_to_the_ghost_path_for_a_deleted_changed_module():
    # path_by_name includes a "ghost" node's historical path, exactly as
    # cli.run_analysis provides for a deleted or renamed-away module.
    impact = ImpactResult(
        impacted={"tests.test_login", "app.crypto"},
        parents={"tests.test_login": "app.crypto"},
    )
    path_by_name = _paths("tests.test_login", "app.crypto")

    result = explain_selection(
        Path("tests/test_login.py"), "tests.test_login", impact, path_by_name
    )

    assert result.changed_path == Path("app/crypto.py")


# --- format_explanation: pure rendering, ASCII-only ---


def test_format_not_found():
    text = format_explanation(
        Explanation(test_path=Path("tests/test_x.py"), found=False)
    )
    assert "not a known module" in text


def test_format_not_selected():
    text = format_explanation(
        Explanation(test_path=Path("tests/test_x.py"), found=True, selected=False)
    )
    assert text == "tests/test_x.py was not selected."


def test_format_self_changed():
    text = format_explanation(
        Explanation(
            test_path=Path("tests/test_x.py"),
            found=True,
            selected=True,
            self_changed=True,
        )
    )
    assert text == "tests/test_x.py was selected because it is itself changed."


def test_format_chain_is_ascii_and_increasingly_indented():
    explanation = Explanation(
        test_path=Path("tests/test_login.py"),
        found=True,
        selected=True,
        path=[
            Path("tests/test_login.py"),
            Path("app/login.py"),
            Path("app/tokens.py"),
        ],
        changed_path=Path("app/tokens.py"),
    )
    text = format_explanation(explanation)

    assert text == (
        "tests/test_login.py\n"
        "    imports app/login.py\n"
        "        imports app/tokens.py\n"
        "\n"
        "Changed module:\n"
        "    app/tokens.py"
    )
    text.encode("ascii")  # must never raise -- spec §19's own arrow glyph does
