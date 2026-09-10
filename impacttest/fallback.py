"""Conservative full-suite fallback policy (Phase 10, spec §16).

Three independent triggers, checked in order; the first one that fires
wins, since only one reason needs to be shown to the user. All of them
share the same shape: when static analysis cannot be trusted for some
part of the repository, the safe answer is "run everything", not a guess
at a smaller set (spec §15's "prefer false positives over false
negatives").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from impacttest.config import Config
from impacttest.models import ChangeSet
from impacttest.resolver import ResolvedModule

_GLOBAL_FILENAMES = {"conftest.py", "pytest.ini"}
_PYPROJECT_TOML = "pyproject.toml"


@dataclass
class FallbackDecision:
    """Whether the full suite must run instead of the computed selection,
    and why -- carried through to both `analyze` and `explain` output
    (spec §19's fallback example) rather than left implicit in a bigger
    selected-tests list.
    """

    triggered: bool = False
    reason: str | None = None


def check_global_files(changes: ChangeSet, config: Config) -> FallbackDecision:
    """spec §16's file-based triggers: any ``conftest.py`` or
    ``pytest.ini`` anywhere in the repo, any change to ``pyproject.toml``
    at all, and anything the project explicitly listed in
    ``global_files``.

    ``pyproject.toml`` triggers on any change to the whole file rather
    than only a change inside its ``[tool.pytest.ini_options]`` table --
    diffing one TOML table out of a file is precision this policy doesn't
    need, and an unnecessary full-suite run costs seconds where a missed
    pytest-config change costs trust in every selection after it.
    """
    changed_paths = [
        *changes.added,
        *changes.modified,
        *changes.deleted,
        *changes.non_python,
        *(p for pair in changes.renamed for p in pair),
    ]

    global_files = set(config.global_files)
    for path in changed_paths:
        posix = path.as_posix()
        if path.name in _GLOBAL_FILENAMES or path.name == _PYPROJECT_TOML:
            return FallbackDecision(triggered=True, reason=f"{posix} changed")
        if posix in global_files:
            return FallbackDecision(triggered=True, reason=f"{posix} changed")

    return FallbackDecision()


def check_analysis_failures(failed_paths: Iterable[Path]) -> FallbackDecision:
    """spec §16: "repository analysis fails". A file that can't be read or
    parsed has completely unknown dependencies -- not just "uncertain
    within a chain we did manage to trace", but no information at all,
    for a file that could in principle be imported from anywhere. That is
    a strictly bigger unknown than one uncertain module inside an
    otherwise-known impacted set, so it is checked across the whole
    repository, not just the impacted closure.
    """
    failed = sorted(failed_paths, key=lambda p: p.as_posix())
    if not failed:
        return FallbackDecision()
    return FallbackDecision(
        triggered=True, reason=f"analysis failed for {failed[0].as_posix()}"
    )


def check_uncertain_impacted(
    impacted: set[str], resolved: Iterable[ResolvedModule]
) -> FallbackDecision:
    """spec §16's local conservative fallback, MVP policy: if a changed
    module or one of its reverse dependents is uncertain, run all tests.
    Scoped to ``impacted`` (not the whole repository) because an uncertain
    module *outside* the traversal that reached this test genuinely has no
    bearing on it -- unlike an unparseable file (above), whose unknown
    edges could reach anywhere.
    """
    uncertain = sorted(
        (module for module in resolved if module.uncertain and module.name in impacted),
        key=lambda module: module.name,
    )
    if not uncertain:
        return FallbackDecision()

    first = uncertain[0]
    reason = first.reasons[0] if first.reasons else f"'{first.name}' is uncertain"
    return FallbackDecision(triggered=True, reason=reason)


def decide_fallback(
    changes: ChangeSet,
    config: Config,
    failed_paths: Iterable[Path],
    impacted: set[str],
    resolved: Iterable[ResolvedModule],
    all_on_uncertain: bool = True,
) -> FallbackDecision:
    """Run every trigger in priority order, stopping at the first hit."""
    decision = check_global_files(changes, config)
    if decision.triggered:
        return decision

    decision = check_analysis_failures(failed_paths)
    if decision.triggered:
        return decision

    if all_on_uncertain:
        decision = check_uncertain_impacted(impacted, resolved)
        if decision.triggered:
            return decision

    return FallbackDecision()
