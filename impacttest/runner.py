"""Invoke pytest on the selected test files (Phase 8, spec §18)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def run_selected_tests(repo_root: Path, tests: list[Path]) -> int:
    """Run pytest against exactly ``tests``, relative to ``repo_root``.

    Nothing here captures pytest's output -- ``subprocess.run`` with no
    ``stdout``/``capture_output`` inherits this process's own streams, so
    pytest's output reaches the terminal live rather than being buffered
    and dumped at the end (spec §18). The exit code comes back unchanged
    for the same reason: a failing test must fail the tool, not get
    reinterpreted into some other status.

    An empty selection means nothing is affected, not that something went
    wrong -- printing pytest's own "no tests ran" message would suggest a
    misconfiguration where there is none, and running pytest with zero
    path arguments would run its *entire* discovered suite, the opposite
    of an empty selection. So this returns 0 without invoking pytest at
    all (spec §18).
    """
    if not tests:
        print("No affected tests found.")
        print("0 tests selected.")
        return 0

    print(f"{len(tests)} test file(s) selected:")
    for test in tests:
        print(f"  {test.as_posix()}")
    print()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", *(str(test) for test in tests)],
        cwd=repo_root,
        check=False,
    )
    return result.returncode
