"""End-to-end integration tests over real temporary Git repositories (Phase 11).

Every test here drives ``impacttest.cli.main`` -- the same entry point the
console script calls -- against a repository actually created on disk and
committed with ``git``. Nothing is mocked: the diff comes from Git, the
imports come from files, and in the last tests pytest really runs. Spec §25
asks for exactly this ("these tests are more valuable than mocking every Git
operation"), because the failure this project cannot afford -- a test that
should have been selected and wasn't -- is a failure of the whole pipeline
agreeing with itself, which no single unit test can see.
"""

from __future__ import annotations

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from impacttest.cli import main

# --- building repositories on disk ---


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(
        root,
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-q",
        "-m",
        message,
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# Puts src/ on sys.path so the fixture repository's own tests can say
# "from app import a" when pytest really runs them, the way a src-layout
# project would. Committed as part of the baseline -- it only matters to
# the analyzer when it *changes* (spec §16), which one test below does
# deliberately.
_CONFTEST = (
    "import sys\n"
    "from pathlib import Path\n"
    "\n"
    'sys.path.insert(0, str(Path(__file__).parent / "src"))\n'
)


def _init_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a Git repository containing ``files``, committed as the
    baseline, on a branch named ``main`` (the configured default base).
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    for rel, content in files.items():
        _write(repo / rel, content)
    _commit(repo, "baseline")
    _git(repo, "branch", "-q", "-m", "main")
    return repo


def _baseline_repo(tmp_path: Path) -> Path:
    """The repository shape from spec §25, with a real three-module chain::

        tests/test_a.py -> app.a
        tests/test_c.py -> app.c -> app.b -> app.a

    No pyproject.toml: ``source_roots=["src"]``, ``test_roots=["tests"]``
    and ``base_branch="main"`` are the defaults, so this also covers the
    "repository with no ImpactTest config" case (spec §21).
    """
    return _init_repo(
        tmp_path,
        {
            ".gitignore": "__pycache__/\n.pytest_cache/\n",
            "conftest.py": _CONFTEST,
            "README.md": "fixture repository\n",
            "src/app/__init__.py": "",
            "src/app/a.py": "def value():\n    return 1\n",
            "src/app/b.py": (
                "from app import a\n\n\ndef doubled():\n    return a.value() * 2\n"
            ),
            "src/app/c.py": (
                "from app import b\n\n\ndef quadrupled():\n    return b.doubled() * 2\n"
            ),
            "tests/test_a.py": (
                "from app import a\n\n\ndef test_value():\n    assert a.value() == 1\n"
            ),
            "tests/test_c.py": (
                "from app import c\n\n\n"
                "def test_quadrupled():\n    assert c.quadrupled() == 4\n"
            ),
        },
    )


# --- driving the CLI and reading its output ---


def _analyze(repo: Path, capsys, *extra: str) -> tuple[int, str]:
    code = main(["analyze", "--root", str(repo), *extra])
    return code, capsys.readouterr().out


def _section(output: str, heading: str) -> list[str]:
    """The indented paths listed under ``heading`` in an analyze report."""
    lines = output.splitlines()
    listed = []
    for line in lines[lines.index(heading) + 1 :]:
        if not line.strip():
            break
        listed.append(line.strip())
    return [] if listed == ["(none)"] else listed


def _affected(output: str) -> list[str]:
    return _section(output, "Affected tests:")


def _changed(output: str) -> list[str]:
    return _section(output, "Changed:")


# --- scenarios ---


def test_unchanged_repository_selects_nothing(tmp_path: Path, capsys) -> None:
    repo = _baseline_repo(tmp_path)

    code, output = _analyze(repo, capsys)

    assert code == 0
    assert _changed(output) == []
    assert _affected(output) == []
    assert "0 / 2 test files selected" in output


def test_changing_a_leaf_module_selects_only_its_own_test(
    tmp_path: Path, capsys
) -> None:
    # app.c sits at the top of the chain: only test_c reaches it, so
    # test_a must stay unselected (spec §23 Req 4).
    repo = _baseline_repo(tmp_path)
    _write(
        repo / "src" / "app" / "c.py",
        "from app import b\n\n\ndef quadrupled():\n    return b.doubled() + 2\n",
    )

    code, output = _analyze(repo, capsys)

    assert code == 0
    assert _changed(output) == ["src/app/c.py"]
    assert _affected(output) == ["tests/test_c.py"]
    assert "1 / 2 test files selected" in output
    assert "50.0% test-file reduction" in output


def test_changing_a_mid_chain_module_selects_transitively(
    tmp_path: Path, capsys
) -> None:
    # No test imports app.b directly -- test_c only reaches it through
    # app.c, so selecting it proves the reverse traversal is transitive
    # and not merely one hop deep (spec §23 Req 3).
    repo = _baseline_repo(tmp_path)
    _write(
        repo / "src" / "app" / "b.py",
        "from app import a\n\n\ndef doubled():\n    return a.value() + a.value()\n",
    )

    code, output = _analyze(repo, capsys)

    assert code == 0
    assert _affected(output) == ["tests/test_c.py"]


def test_changing_the_base_module_selects_every_dependent_test(
    tmp_path: Path, capsys
) -> None:
    repo = _baseline_repo(tmp_path)
    _write(repo / "src" / "app" / "a.py", "def value():\n    return 1 + 0\n")

    code, output = _analyze(repo, capsys)

    assert code == 0
    assert _affected(output) == ["tests/test_a.py", "tests/test_c.py"]
    assert "2 / 2 test files selected" in output


def test_changing_only_a_test_file_selects_just_that_test(
    tmp_path: Path, capsys
) -> None:
    # Req 1: a changed test file is selected because it changed, even
    # though nothing in the graph depends on it.
    repo = _baseline_repo(tmp_path)
    _write(
        repo / "tests" / "test_a.py",
        "from app import a\n\n\ndef test_value():\n    assert a.value() > 0\n",
    )

    code, output = _analyze(repo, capsys)

    assert code == 0
    assert _affected(output) == ["tests/test_a.py"]


def test_deleting_a_module_still_selects_its_dependents(tmp_path: Path, capsys) -> None:
    # test_c keeps its "from app import c" after app.c is deleted. The
    # module is gone from the working tree, so discovery cannot see it --
    # spec §10 still requires its dependents to be selected.
    repo = _baseline_repo(tmp_path)
    (repo / "src" / "app" / "c.py").unlink()

    code, output = _analyze(repo, capsys)

    assert code == 0
    assert _changed(output) == ["src/app/c.py"]
    assert _affected(output) == ["tests/test_c.py"]


def test_changing_conftest_triggers_the_full_suite(tmp_path: Path, capsys) -> None:
    repo = _baseline_repo(tmp_path)
    _write(repo / "conftest.py", _CONFTEST + "\ncollect_ignore = []\n")

    code, output = _analyze(repo, capsys)

    assert code == 0
    assert _affected(output) == ["tests/test_a.py", "tests/test_c.py"]
    assert "Full-suite safety fallback triggered." in output
    assert "Reason: conftest.py changed" in output


def test_explain_reports_the_fallback_reason_for_an_unrelated_test(
    tmp_path: Path, capsys
) -> None:
    repo = _baseline_repo(tmp_path)
    _write(repo / "conftest.py", _CONFTEST + "\ncollect_ignore = []\n")

    code = main(["explain", "tests/test_a.py", "--root", str(repo)])
    output = capsys.readouterr().out

    assert code == 0
    assert "full-suite safety fallback" in output
    assert "conftest.py changed" in output


def test_explain_renders_the_whole_chain_through_the_cli(
    tmp_path: Path, capsys
) -> None:
    repo = _baseline_repo(tmp_path)
    _write(repo / "src" / "app" / "a.py", "def value():\n    return 1 + 0\n")

    code = main(["explain", "tests/test_c.py", "--root", str(repo)])
    output = capsys.readouterr().out

    assert code == 0
    assert output.strip() == (
        "tests/test_c.py\n"
        "    imports src/app/c.py\n"
        "        imports src/app/b.py\n"
        "            imports src/app/a.py\n"
        "\n"
        "Changed module:\n"
        "    src/app/a.py"
    )


def test_cyclic_imports_terminate(tmp_path: Path, capsys) -> None:
    # app.a imports app.b at module level; app.b imports app.a from inside
    # a function. Statically that is a cycle (the analyzer recurses into
    # function bodies, Phase 2), so the reverse-BFS visited set is the only
    # thing keeping the traversal from looping -- Req 5. Keeping app.b's
    # import nested also leaves the fixture genuinely importable.
    repo = _init_repo(
        tmp_path,
        {
            "conftest.py": _CONFTEST,
            "src/app/__init__.py": "",
            "src/app/a.py": "from app import b\n\n\ndef go():\n    return b.pong()\n",
            "src/app/b.py": (
                "def pong():\n    from app import a\n\n    return a is not None\n"
            ),
            "tests/test_cycle.py": (
                "from app import a\n\n\ndef test_go():\n    assert a.go()\n"
            ),
        },
    )
    _write(
        repo / "src" / "app" / "a.py",
        "from app import b\n\n\ndef go():\n    return b.pong() is True\n",
    )

    # Bounded, so a regression that reintroduces an unbounded traversal
    # fails this test instead of hanging the suite with no explanation.
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        code = executor.submit(main, ["analyze", "--root", str(repo)]).result(
            timeout=60
        )
    finally:
        executor.shutdown(wait=False)
    output = capsys.readouterr().out

    assert code == 0
    assert _affected(output) == ["tests/test_cycle.py"]


def test_run_with_nothing_affected_exits_zero_without_pytest(
    tmp_path: Path, capsys
) -> None:
    # A non-Python file that is not a global-fallback trigger: no module
    # changed, so nothing is selected and pytest is never invoked (§18).
    repo = _baseline_repo(tmp_path)
    _write(repo / "README.md", "fixture repository, edited\n")

    code = main(["run", "--root", str(repo)])
    output = capsys.readouterr().out

    assert code == 0
    assert "No affected tests found." in output
    assert "passed" not in output


# --- end-to-end: impacttest run vs. plain pytest ---

_PROGRESS = re.compile(r"^(\S+\.py) ([.FEsxX]+)", re.MULTILINE)


def _outcomes(output: str) -> dict[str, str]:
    """Per-file result characters from pytest's progress lines, e.g.
    ``{"tests/test_a.py": ".", "tests/test_c.py": "F"}``.

    pytest prints those paths with the platform separator, so they are
    normalized to forward slashes here -- everything else in this file,
    including ImpactTest's own output, is posix-style.
    """
    return {
        path.replace("\\", "/"): status for path, status in _PROGRESS.findall(output)
    }


def _plain_pytest(repo: Path, *paths: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *paths],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout


def test_run_agrees_with_plain_pytest(tmp_path: Path, capfd) -> None:
    """The end-to-end check: for a change that really breaks a test,
    ``impacttest run`` must select the failing test, report the same
    per-file outcomes plain pytest does, and hand back pytest's exit code.

    ``capfd`` rather than ``capsys`` because the runner deliberately lets
    pytest inherit this process's file descriptors so its output streams
    live (§18) -- that output never passes through ``sys.stdout``.
    """
    repo = _baseline_repo(tmp_path)
    # quadrupled() now returns 6, not 4: test_c fails, test_a is untouched.
    _write(
        repo / "src" / "app" / "c.py",
        "from app import b\n\n\ndef quadrupled():\n    return b.doubled() + 4\n",
    )

    full_code, full_output = _plain_pytest(repo)
    full_outcomes = _outcomes(full_output)
    assert full_outcomes == {"tests/test_a.py": ".", "tests/test_c.py": "F"}
    assert full_code != 0

    capfd.readouterr()
    run_code = main(["run", "--root", str(repo)])
    run_output = capfd.readouterr().out

    # Every file that failed under the full suite was selected -- the
    # safety property the whole tool rests on.
    listed = {
        line.strip() for line in run_output.splitlines() if line.strip().endswith(".py")
    }
    failing = {path for path, status in full_outcomes.items() if "F" in status}
    assert failing <= listed

    # Same files, same outcomes, same exit code as running pytest on the
    # selection directly.
    run_outcomes = _outcomes(run_output)
    assert run_outcomes == {"tests/test_c.py": "F"}
    selected_code, selected_output = _plain_pytest(repo, "tests/test_c.py")
    assert run_outcomes == _outcomes(selected_output)
    assert run_code == selected_code == full_code != 0


def test_run_returns_zero_when_the_selected_tests_pass(tmp_path: Path, capfd) -> None:
    repo = _baseline_repo(tmp_path)
    # Behaviour-preserving edit: app.b still doubles, so test_c passes.
    _write(
        repo / "src" / "app" / "b.py",
        "from app import a\n\n\ndef doubled():\n    return a.value() + a.value()\n",
    )

    capfd.readouterr()
    run_code = main(["run", "--root", str(repo)])
    run_output = capfd.readouterr().out

    assert run_code == 0
    assert _outcomes(run_output) == {"tests/test_c.py": "."}
