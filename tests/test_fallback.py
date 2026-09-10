from pathlib import Path

from impacttest.config import Config
from impacttest.fallback import (
    check_analysis_failures,
    check_global_files,
    check_uncertain_impacted,
    decide_fallback,
)
from impacttest.models import ChangeSet
from impacttest.resolver import ResolvedModule


def _resolved(name: str, *, uncertain: bool = False, reasons=()) -> ResolvedModule:
    return ResolvedModule(
        name=name,
        path=Path(f"{name.replace('.', '/')}.py"),
        uncertain=uncertain,
        reasons=tuple(reasons),
    )


# --- check_global_files ---


def test_conftest_py_anywhere_triggers_fallback():
    changes = ChangeSet(modified=[Path("tests/conftest.py")])
    decision = check_global_files(changes, Config())
    assert decision.triggered is True
    assert decision.reason == "tests/conftest.py changed"


def test_nested_conftest_py_also_triggers():
    changes = ChangeSet(modified=[Path("tests/auth/conftest.py")])
    decision = check_global_files(changes, Config())
    assert decision.triggered is True


def test_pytest_ini_triggers_fallback():
    changes = ChangeSet(non_python=[Path("pytest.ini")])
    decision = check_global_files(changes, Config())
    assert decision.triggered is True
    assert decision.reason == "pytest.ini changed"


def test_pyproject_toml_triggers_fallback():
    changes = ChangeSet(non_python=[Path("pyproject.toml")])
    decision = check_global_files(changes, Config())
    assert decision.triggered is True
    assert decision.reason == "pyproject.toml changed"


def test_configured_global_file_triggers_fallback():
    changes = ChangeSet(modified=[Path("shared/fixtures.py")])
    config = Config(global_files=["shared/fixtures.py"])
    decision = check_global_files(changes, config)
    assert decision.triggered is True
    assert decision.reason == "shared/fixtures.py changed"


def test_unconfigured_file_does_not_trigger():
    changes = ChangeSet(modified=[Path("src/app/unrelated.py")])
    decision = check_global_files(changes, Config())
    assert decision.triggered is False
    assert decision.reason is None


def test_deleted_and_renamed_paths_are_checked_too():
    changes = ChangeSet(deleted=[Path("pytest.ini")])
    assert check_global_files(changes, Config()).triggered is True

    changes = ChangeSet(renamed=[(Path("old_conftest.py"), Path("tests/conftest.py"))])
    assert check_global_files(changes, Config()).triggered is True


# --- check_analysis_failures ---


def test_no_failures_does_not_trigger():
    assert check_analysis_failures([]).triggered is False


def test_any_failed_file_triggers_regardless_of_impact():
    # spec §16: "repository analysis fails" is a whole-repository check,
    # not scoped to the impacted closure -- an unparseable file's edges
    # are completely unknown, not just uncertain within a traced chain.
    decision = check_analysis_failures([Path("src/app/broken.py")])
    assert decision.triggered is True
    assert decision.reason == "analysis failed for src/app/broken.py"


def test_multiple_failures_report_the_first_by_path():
    decision = check_analysis_failures([Path("z.py"), Path("a.py")])
    assert decision.reason == "analysis failed for a.py"


# --- check_uncertain_impacted ---


def test_uncertain_module_outside_impacted_set_does_not_trigger():
    resolved = [_resolved("app.dynamic", uncertain=True, reasons=("dynamic",))]
    decision = check_uncertain_impacted(impacted={"app.other"}, resolved=resolved)
    assert decision.triggered is False


def test_uncertain_module_inside_impacted_set_triggers():
    resolved = [_resolved("app.dynamic", uncertain=True, reasons=("uses eval",))]
    decision = check_uncertain_impacted(impacted={"app.dynamic"}, resolved=resolved)
    assert decision.triggered is True
    assert decision.reason == "uses eval"


def test_uncertain_module_with_no_reason_gets_a_fallback_message():
    resolved = [_resolved("app.dynamic", uncertain=True, reasons=())]
    decision = check_uncertain_impacted(impacted={"app.dynamic"}, resolved=resolved)
    assert decision.triggered is True
    assert "app.dynamic" in (decision.reason or "")


def test_certain_modules_in_impacted_set_do_not_trigger():
    resolved = [_resolved("app.a"), _resolved("app.b")]
    decision = check_uncertain_impacted(impacted={"app.a", "app.b"}, resolved=resolved)
    assert decision.triggered is False


# --- decide_fallback: priority order ---


def test_decide_fallback_prefers_global_files_over_other_checks():
    changes = ChangeSet(non_python=[Path("pytest.ini")])
    resolved = [_resolved("app.dynamic", uncertain=True)]
    decision = decide_fallback(
        changes, Config(), [Path("broken.py")], {"app.dynamic"}, resolved
    )
    assert decision.reason == "pytest.ini changed"


def test_decide_fallback_falls_through_to_uncertain_check():
    changes = ChangeSet()
    resolved = [_resolved("app.dynamic", uncertain=True, reasons=("uses eval",))]
    decision = decide_fallback(changes, Config(), [], {"app.dynamic"}, resolved)
    assert decision.triggered is True
    assert decision.reason == "uses eval"


def test_decide_fallback_respects_all_on_uncertain_false():
    changes = ChangeSet()
    resolved = [_resolved("app.dynamic", uncertain=True, reasons=("uses eval",))]
    decision = decide_fallback(
        changes, Config(), [], {"app.dynamic"}, resolved, all_on_uncertain=False
    )
    assert decision.triggered is False


def test_decide_fallback_returns_not_triggered_when_nothing_fires():
    decision = decide_fallback(ChangeSet(), Config(), [], set(), [])
    assert decision.triggered is False
    assert decision.reason is None
