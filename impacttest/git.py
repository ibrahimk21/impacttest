"""Git change detection between two revisions (Phase 6, spec §10)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from impacttest.models import ChangeSet

WORKING_TREE = "working-tree"


def get_merge_base(repo_root: Path, base: str, head: str = "HEAD") -> str:
    """Resolve the merge-base commit of ``base`` and ``head``."""
    return _run_git(repo_root, ["merge-base", base, head]).strip()


def get_changed_files(
    repo_root: Path, base: str, head: str = WORKING_TREE
) -> ChangeSet:
    """Classify the files changed between ``base`` and ``head``.

    ``head == "working-tree"`` -- the CLI default (spec §7) -- diffs the
    merge-base of ``base`` and the current ``HEAD`` against the working
    tree. A ``git diff`` against a single commit compares that commit to the
    working tree directly, so it captures both commits made since diverging
    from ``base`` and any uncommitted edits, staged or not, in one pass.

    Any other ``head`` is treated as a fixed revision and diffed against
    ``base`` with triple-dot (merge-base) semantics -- ``git diff
    --name-status base...head`` exactly, per spec §10 -- so work landed on
    ``base`` after the branch under test diverged from it never shows up as
    a "change" on that branch.
    """
    if head == WORKING_TREE:
        diff_range = [get_merge_base(repo_root, base)]
    else:
        diff_range = [f"{base}...{head}"]

    output = _run_git(
        repo_root,
        ["-c", "core.quotepath=false", "diff", "--name-status", *diff_range],
    )
    changes = parse_name_status(output)

    if head == WORKING_TREE:
        # `git diff <commit>` only ever reports tracked files -- a brand new
        # file that hasn't been `git add`-ed yet is invisible to it, so a
        # newly written module would silently get no analysis at all unless
        # untracked files are folded in here too.
        _add_untracked(repo_root, changes)

    return changes


def get_diff_start(repo_root: Path, base: str, head: str = WORKING_TREE) -> str:
    """The revision a deleted path last existed at -- exactly the tree
    ``get_changed_files`` diffs *from*.

    Discovery only ever sees the current tree, so a module gone from disk
    has no ``Module`` for the analyzer to read; recovering its last content
    (to find who still depends on it, spec §10) means reading it out of
    history instead, from this same starting point.
    """
    effective_head = "HEAD" if head == WORKING_TREE else head
    return get_merge_base(repo_root, base, effective_head)


def read_file_at_revision(repo_root: Path, revision: str, rel_path: Path) -> str | None:
    """The text of ``rel_path`` as it existed at ``revision``, or None if
    it didn't exist there. ``None`` is a normal outcome -- callers use this
    to recover a deleted file's last content, and a path that never
    existed at ``revision`` either (e.g. it was itself added and later
    renamed away within the diff range) is simply nothing to recover.
    """
    try:
        result = subprocess.run(
            ["git", "show", f"{revision}:{rel_path.as_posix()}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError:
        return None
    return result.stdout


def _add_untracked(repo_root: Path, changes: ChangeSet) -> None:
    output = _run_git(repo_root, ["ls-files", "--others", "--exclude-standard"])
    for line in output.splitlines():
        if not line:
            continue
        path = normalize_git_path(line)
        (changes.added if path.suffix == ".py" else changes.non_python).append(path)


def parse_name_status(output: str) -> ChangeSet:
    """Turn ``git diff --name-status`` output into a classified ChangeSet.

    Each line is ``<status>\\t<path>``, or ``<status>\\t<old>\\t<new>`` for a
    rename (the ``R###`` status carries a similarity percentage we don't
    need). A status this parser doesn't recognize -- a type change (``T``),
    an unmerged path (``U``), anything else Git might emit -- is folded into
    ``modified`` for its listed path(s) rather than dropped: an unclassified
    change getting no analysis at all is exactly the false negative this
    tool exists to avoid.

    A ``.py`` path lands in the matching bucket; anything else lands in
    ``non_python``, since it can never produce a graph edge but Phase 10's
    global-file fallback still needs to see it. A rename is only recorded as
    a Python rename if both sides are ``.py``. A rename across the boundary
    (``notes.txt`` -> ``notes.py``) is instead split into its two halves --
    the ``.py`` side really is a new module that needs analysis, and folding
    it into ``non_python`` alongside its non-``.py`` counterpart would drop
    it from the graph entirely.
    """
    changes = ChangeSet()
    status_buckets = {"A": changes.added, "M": changes.modified, "D": changes.deleted}

    for line in output.splitlines():
        if not line:
            continue
        status, *paths = line.split("\t")

        if status.startswith("R"):
            old_path, new_path = (
                normalize_git_path(paths[0]),
                normalize_git_path(paths[1]),
            )
            if old_path.suffix == ".py" and new_path.suffix == ".py":
                changes.renamed.append((old_path, new_path))
            else:
                old_bucket = (
                    changes.deleted if old_path.suffix == ".py" else changes.non_python
                )
                new_bucket = (
                    changes.added if new_path.suffix == ".py" else changes.non_python
                )
                old_bucket.append(old_path)
                new_bucket.append(new_path)
            continue

        path = normalize_git_path(paths[0])
        bucket = status_buckets.get(status, changes.modified)
        (bucket if path.suffix == ".py" else changes.non_python).append(path)

    return changes


def normalize_git_path(raw: str) -> Path:
    """Convert one of Git's forward-slash, repo-root-relative paths to a
    ``Path``. ``Path`` accepts either separator on Windows, so this is
    correct as-is; it exists to make that conversion an explicit, tested
    decision rather than an incidental one, matching how the rest of the
    codebase (e.g. discovery.py) treats repo-relative paths.
    """
    return Path(raw)


def _run_git(repo_root: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git is not installed or not on PATH") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo_root}: {exc.stderr}"
        ) from exc
    return result.stdout
