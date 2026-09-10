import subprocess
from pathlib import Path

from impacttest.git import (
    get_changed_files,
    get_diff_start,
    normalize_git_path,
    parse_name_status,
    read_file_at_revision,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _commit(root: Path, message: str) -> str:
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
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# --- parse_name_status: pure parsing, no subprocess needed ---


def test_added_status_is_classified():
    changes = parse_name_status("A\tsrc/new_module.py\n")
    assert changes.added == [Path("src/new_module.py")]


def test_modified_status_is_classified():
    changes = parse_name_status("M\tsrc/existing.py\n")
    assert changes.modified == [Path("src/existing.py")]


def test_deleted_status_is_classified():
    changes = parse_name_status("D\tsrc/gone.py\n")
    assert changes.deleted == [Path("src/gone.py")]


def test_rename_status_carries_both_paths():
    changes = parse_name_status("R100\told/name.py\tnew/name.py\n")
    assert changes.renamed == [(Path("old/name.py"), Path("new/name.py"))]
    assert changes.added == []
    assert changes.modified == []


def test_rename_across_extensions_splits_into_deleted_and_added():
    # notes.py is a genuinely new Python file and must still reach the
    # graph, even though the rename itself isn't a Python-to-Python one.
    changes = parse_name_status("R087\tnotes.txt\tnotes.py\n")
    assert changes.renamed == []
    assert changes.non_python == [Path("notes.txt")]
    assert changes.added == [Path("notes.py")]


def test_rename_away_from_python_is_treated_as_deleted():
    changes = parse_name_status("R087\told.py\tnew.txt\n")
    assert changes.renamed == []
    assert changes.deleted == [Path("old.py")]
    assert changes.non_python == [Path("new.txt")]


def test_non_python_change_is_kept_separate():
    changes = parse_name_status("M\tpyproject.toml\n")
    assert changes.modified == []
    assert changes.non_python == [Path("pyproject.toml")]


def test_unrecognized_status_code_falls_back_to_modified_not_dropped():
    # "T" (type change, e.g. file -> symlink) isn't one of A/M/D/R, but a
    # change this parser can't classify must still surface somewhere.
    changes = parse_name_status("T\tsrc/weird.py\n")
    assert changes.modified == [Path("src/weird.py")]


def test_multiple_lines_are_each_classified():
    output = "A\tsrc/a.py\nM\tsrc/b.py\nD\tsrc/c.py\n"
    changes = parse_name_status(output)
    assert changes.added == [Path("src/a.py")]
    assert changes.modified == [Path("src/b.py")]
    assert changes.deleted == [Path("src/c.py")]


def test_empty_output_is_no_changes():
    changes = parse_name_status("")
    assert changes.added == []
    assert changes.modified == []
    assert changes.deleted == []
    assert changes.renamed == []
    assert changes.non_python == []


# --- path normalization ---


def test_normalize_git_path_round_trips_forward_slashes():
    path = normalize_git_path("src/app/auth.py")
    assert path.as_posix() == "src/app/auth.py"
    assert path.parts == ("src", "app", "auth.py")


def test_normalize_git_path_handles_single_component():
    path = normalize_git_path("conftest.py")
    assert path.as_posix() == "conftest.py"


# --- get_changed_files against a real repo (Phase 6 gate) ---


def test_get_changed_files_working_tree_default(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    _write(tmp_path / "pyproject.toml", "[tool.impacttest]\n")
    _commit(tmp_path, "baseline")
    _git(tmp_path, "branch", "-q", "-m", "main")

    # Uncommitted changes on top of base, exercising the "working-tree"
    # default head (spec §7) rather than a fixed revision.
    _write(tmp_path / "src" / "app.py", "x = 2\n")
    _write(tmp_path / "src" / "new_thing.py", "y = 1\n")
    (tmp_path / "pyproject.toml").unlink()

    changes = get_changed_files(tmp_path, base="main")

    assert changes.modified == [Path("src/app.py")]
    assert changes.added == [Path("src/new_thing.py")]
    assert changes.non_python == [Path("pyproject.toml")]


def test_get_changed_files_between_two_commits(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    _write(tmp_path / "src" / "keep.py", "z = 1\n")
    base_sha = _commit(tmp_path, "baseline")
    _git(tmp_path, "branch", "-q", "-m", "main")

    _git(tmp_path, "mv", "src/keep.py", "src/kept.py")
    _write(tmp_path / "src" / "app.py", "x = 2\n")
    head_sha = _commit(tmp_path, "rename and edit")

    changes = get_changed_files(tmp_path, base=base_sha, head=head_sha)

    assert changes.modified == [Path("src/app.py")]
    assert changes.renamed == [(Path("src/keep.py"), Path("src/kept.py"))]


def test_get_changed_files_uses_merge_base_not_base_tip(tmp_path: Path) -> None:
    # A commit made on main *after* the feature branch diverged must not
    # show up as a "change" on the feature branch (spec §10 merge-base
    # semantics) -- otherwise switching main underneath a stale branch
    # would silently select unrelated tests.
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "shared.py", "x = 1\n")
    _commit(tmp_path, "baseline")
    _git(tmp_path, "branch", "-q", "-m", "main")

    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _write(tmp_path / "src" / "feature_only.py", "y = 1\n")
    _commit(tmp_path, "feature work")

    _git(tmp_path, "checkout", "-q", "main")
    _write(tmp_path / "src" / "main_only.py", "z = 1\n")
    _commit(tmp_path, "unrelated main work")

    _git(tmp_path, "checkout", "-q", "feature")
    changes = get_changed_files(tmp_path, base="main")

    assert changes.added == [Path("src/feature_only.py")]


# --- reading deleted files back out of history ---


def test_get_diff_start_is_the_merge_base_for_working_tree(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    base_sha = _commit(tmp_path, "baseline")
    _git(tmp_path, "branch", "-q", "-m", "main")

    _write(tmp_path / "src" / "app.py", "x = 2\n")  # uncommitted

    assert get_diff_start(tmp_path, base="main") == base_sha


def test_get_diff_start_for_a_fixed_head_uses_merge_base_of_both(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    base_sha = _commit(tmp_path, "baseline")
    _git(tmp_path, "branch", "-q", "-m", "main")

    # A separate branch, so head_sha genuinely diverges from main instead
    # of just being main's new tip.
    _git(tmp_path, "checkout", "-q", "-b", "feature")
    _write(tmp_path / "src" / "app.py", "x = 2\n")
    head_sha = _commit(tmp_path, "change")

    assert get_diff_start(tmp_path, base="main", head=head_sha) == base_sha


def test_read_file_at_revision_returns_content_that_existed_there(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    base_sha = _commit(tmp_path, "baseline")

    assert read_file_at_revision(tmp_path, base_sha, Path("src/app.py")) == "x = 1\n"


def test_read_file_at_revision_returns_none_for_a_path_that_never_existed_there(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    base_sha = _commit(tmp_path, "baseline")

    assert read_file_at_revision(tmp_path, base_sha, Path("src/missing.py")) is None


def test_read_file_at_revision_recovers_a_deleted_files_last_content(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init", "-q")
    _write(tmp_path / "src" / "app.py", "x = 1\n")
    base_sha = _commit(tmp_path, "baseline")

    (tmp_path / "src" / "app.py").unlink()
    _commit(tmp_path, "delete app.py")

    assert read_file_at_revision(tmp_path, base_sha, Path("src/app.py")) == "x = 1\n"
