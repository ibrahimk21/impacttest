import subprocess
from pathlib import Path

from impacttest.cli import AnalysisReport, build_parser, format_report, run_analysis
from impacttest.config import load_config


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


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- format_report: pure formatting, no I/O ---


def test_format_report_with_no_changes():
    report = AnalysisReport(total_tests=8)
    text = format_report(report)
    assert "Changed:\n  (none)" in text
    assert "Affected tests:\n  (none)" in text
    assert "0 / 8 test files selected" in text


def test_format_report_shows_selected_tests_and_reduction():
    report = AnalysisReport(
        changed_paths=[Path("src/auth/tokens.py")],
        selected_tests=[Path("tests/test_login.py"), Path("tests/test_refresh.py")],
        total_tests=86,
    )
    text = format_report(report)
    assert "src/auth/tokens.py" in text
    assert "tests/test_login.py" in text
    assert "tests/test_refresh.py" in text
    assert "2 / 86 test files selected" in text
    assert "97.7% test-file reduction" in text


def test_format_report_skips_reduction_line_with_no_tests():
    text = format_report(AnalysisReport(total_tests=0))
    assert "0 / 0 test files selected" in text
    assert "reduction" not in text


def test_format_report_verbose_shows_uncertain_modules():
    report = AnalysisReport(
        total_tests=1,
        node_count=3,
        edge_count=2,
        uncertain=[("app.dynamic", ("uses a dynamic import construct",))],
    )
    text = format_report(report, verbose=True)
    assert "Modules analyzed: 3 (edges: 2)" in text
    assert "app.dynamic" in text
    assert "uses a dynamic import construct" in text


def test_format_report_not_verbose_hides_module_counts():
    report = AnalysisReport(total_tests=1, node_count=3, edge_count=2)
    text = format_report(report)
    assert "Modules analyzed" not in text


def test_build_parser_analyze_accepts_expected_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "analyze",
            "--base",
            "develop",
            "--head",
            "HEAD",
            "--root",
            ".",
            "--tests",
            "tests",
            "--source",
            "src",
            "--verbose",
        ]
    )
    assert args.command == "analyze"
    assert args.base == "develop"
    assert args.head == "HEAD"
    assert args.tests == ["tests"]
    assert args.source == ["src"]
    assert args.verbose is True


# --- run_analysis against real temporary Git repos ---


def _baseline_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app" / "__init__.py")
    _write(tmp_path / "src" / "app" / "crypto.py", "def hash(x):\n    return x\n")
    _write(
        tmp_path / "src" / "app" / "login.py",
        "from app import crypto\n\ndef login():\n    return crypto.hash('x')\n",
    )
    _write(
        tmp_path / "tests" / "test_login.py",
        "from app import login\n\ndef test_login():\n    login.login()\n",
    )
    _write(
        tmp_path / "tests" / "test_unrelated.py",
        "def test_unrelated():\n    assert True\n",
    )
    _commit(tmp_path, "baseline")
    _git(tmp_path, "branch", "-q", "-m", "main")
    return tmp_path


def test_direct_dependency_is_selected(tmp_path: Path) -> None:
    repo_root = _baseline_repo(tmp_path)
    _write(
        repo_root / "src" / "app" / "crypto.py", "def hash(x):\n    return x[::-1]\n"
    )

    config = load_config(repo_root)
    report = run_analysis(repo_root, config, base="main")

    assert report.selected_tests == [Path("tests/test_login.py")]
    assert report.total_tests == 2


def test_transitive_chain_is_selected(tmp_path: Path) -> None:
    repo_root = _baseline_repo(tmp_path)
    _write(
        repo_root / "src" / "app" / "crypto.py", "def hash(x):\n    return x[::-1]\n"
    )

    config = load_config(repo_root)
    report = run_analysis(repo_root, config, base="main")

    # tests.test_login -> app.login -> app.crypto: two hops, still selected.
    assert Path("tests/test_login.py") in report.selected_tests
    assert Path("tests/test_unrelated.py") not in report.selected_tests


def test_changed_test_file_is_always_selected(tmp_path: Path) -> None:
    repo_root = _baseline_repo(tmp_path)
    _write(
        repo_root / "tests" / "test_unrelated.py",
        "def test_unrelated():\n    assert False\n",
    )

    config = load_config(repo_root)
    report = run_analysis(repo_root, config, base="main")

    assert report.selected_tests == [Path("tests/test_unrelated.py")]


def test_deleted_module_selects_its_stale_importer(tmp_path: Path) -> None:
    # login.py keeps importing crypto after crypto.py is deleted -- a real
    # bug, and exactly the case spec §10 says must still select test_login:
    # the deleted module's dependents are affected.
    repo_root = _baseline_repo(tmp_path)
    (repo_root / "src" / "app" / "crypto.py").unlink()

    config = load_config(repo_root)
    report = run_analysis(repo_root, config, base="main")

    assert Path("tests/test_login.py") in report.selected_tests
    assert Path("src/app/crypto.py") in report.changed_paths


def test_rename_away_selects_stale_importer_of_the_old_name(tmp_path: Path) -> None:
    # crypto.py is renamed to security.py, but login.py is left untouched
    # and still says "from app import crypto" -- app.crypto no longer
    # resolves to a real file, so without the ghost node this import would
    # just look unresolvably internal rather than reaching test_login.
    repo_root = _baseline_repo(tmp_path)
    _git(repo_root, "mv", "src/app/crypto.py", "src/app/security.py")

    config = load_config(repo_root)
    report = run_analysis(repo_root, config, base="main")

    assert Path("tests/test_login.py") in report.selected_tests


def test_full_report_against_this_repository() -> None:
    # The Phase 7 gate: a real "impacttest analyze" run against the tool's
    # own repository must produce a sane, non-empty report.
    from impacttest.discovery import find_repo_root

    repo_root = find_repo_root(Path(__file__).parent)
    config = load_config(repo_root)

    report = run_analysis(repo_root, config, base="HEAD", head="HEAD")

    assert report.changed_paths == []
    assert report.selected_tests == []
    assert report.total_tests > 0
