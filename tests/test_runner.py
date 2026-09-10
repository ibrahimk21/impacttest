import subprocess
from pathlib import Path

from impacttest.runner import run_selected_tests


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_empty_selection_does_not_invoke_pytest(monkeypatch, capsys, tmp_path):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("pytest must not be invoked for an empty selection")

    monkeypatch.setattr("impacttest.runner.subprocess.run", fail_if_called)

    exit_code = run_selected_tests(tmp_path, [])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert output == "No affected tests found.\n0 tests selected.\n"


def test_exit_code_is_zero_on_passing_tests(tmp_path: Path) -> None:
    _write(tmp_path / "test_pass.py", "def test_pass():\n    assert True\n")

    exit_code = run_selected_tests(tmp_path, [Path("test_pass.py")])

    assert exit_code == 0


def test_exit_code_propagates_on_test_failure(tmp_path: Path) -> None:
    _write(tmp_path / "test_fail.py", "def test_fail():\n    assert False\n")

    exit_code = run_selected_tests(tmp_path, [Path("test_fail.py")])

    assert exit_code != 0


def test_only_the_selected_files_run_not_the_whole_directory(tmp_path: Path) -> None:
    # A failing file that isn't in the selection must not affect the
    # result -- otherwise "selected" wouldn't mean anything.
    _write(tmp_path / "test_pass.py", "def test_pass():\n    assert True\n")
    _write(tmp_path / "test_fail.py", "def test_fail():\n    assert False\n")

    exit_code = run_selected_tests(tmp_path, [Path("test_pass.py")])

    assert exit_code == 0


def test_prints_the_selection_before_running(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    # subprocess.run inherits this process's real stdout, so a live pytest
    # child's own output isn't something capsys (a sys.stdout swap) can see
    # at all -- mocking the call instead verifies, deterministically, that
    # the selection is printed as code that runs strictly before pytest is
    # invoked, rather than depending on interleaved subprocess output.
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr("impacttest.runner.subprocess.run", fake_run)

    exit_code = run_selected_tests(tmp_path, [Path("test_pass.py")])

    assert exit_code == 0
    assert len(calls) == 1
    output = capsys.readouterr().out
    assert "1 test file(s) selected:" in output
    assert "test_pass.py" in output
