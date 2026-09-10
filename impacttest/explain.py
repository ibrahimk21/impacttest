"""Reconstruct and render dependency paths for selected tests (Phase 9, spec §19)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from impacttest.impact import ImpactResult


@dataclass
class Explanation:
    """Why (or why not) one test was selected, ready to render.

    ``found`` is False when ``test_path`` doesn't name any module this
    analysis knows about at all -- a typo or a path outside every
    configured root -- which is a different, more useful thing to tell a
    user than "not selected". ``self_changed`` covers the case where the
    test itself is the change (spec §23 Req 1): there is no path to walk,
    only the test.
    """

    test_path: Path
    found: bool = True
    selected: bool = False
    self_changed: bool = False
    path: list[Path] = field(default_factory=list)
    changed_path: Path | None = None
    fallback: bool = False
    fallback_reason: str | None = None


def explain_selection(
    test_path: Path,
    test_name: str,
    impact: ImpactResult,
    path_by_name: dict[str, Path],
    fallback: bool = False,
    fallback_reason: str | None = None,
) -> Explanation:
    """Reconstruct why ``test_name`` was (or wasn't) selected.

    The path is rebuilt by walking ``impact.parents`` backward from the
    test: each entry points one hop closer to the change, and a name with
    no entry is where the walk ends -- the changed module itself (spec
    §19's "internally, this can be implemented by storing parent
    information while performing reverse BFS", which is exactly what
    Phase 5 already does). ``path_by_name`` covers both currently-existing
    modules and the "ghost" nodes ``cli.run_analysis`` reconstructs for
    deleted or renamed-away modules, so a chain that bottoms out at one of
    those still renders a real path instead of a bare dotted name.

    When Phase 10's full-suite fallback is active, every real test module
    is selected regardless of the graph -- there is no dependency chain to
    walk, only the reason the fallback fired (spec §19's fallback
    example), so that check short-circuits before the graph is consulted
    at all. It still comes after the "found" check: a nonexistent path is
    a mistake worth reporting on its own, fallback or not.
    """
    if test_name not in path_by_name:
        return Explanation(test_path=test_path, found=False)

    if fallback:
        return Explanation(
            test_path=test_path,
            found=True,
            selected=True,
            fallback=True,
            fallback_reason=fallback_reason,
        )

    if test_name not in impact.impacted:
        return Explanation(test_path=test_path, found=True, selected=False)

    chain = [test_name]
    current = test_name
    while current in impact.parents:
        current = impact.parents[current]
        chain.append(current)

    return Explanation(
        test_path=test_path,
        found=True,
        selected=True,
        self_changed=len(chain) == 1,
        path=[path_by_name.get(name, Path(name)) for name in chain],
        changed_path=path_by_name.get(current, Path(current)),
    )


def format_explanation(explanation: Explanation) -> str:
    """Render an Explanation as the human-readable text spec §19 describes.

    Uses the increasingly-indented "imports" style from spec §6.3 rather
    than the arrow-glyph style from §19's own example -- plain ASCII prints
    correctly on every terminal, including a default Windows console,
    where a non-ASCII arrow can raise ``UnicodeEncodeError`` outright.
    """
    display = explanation.test_path.as_posix()

    if not explanation.found:
        return (
            f"'{display}' is not a known module under any configured "
            "source or test root."
        )

    if not explanation.selected:
        return f"{display} was not selected."

    if explanation.fallback:
        headline = (
            f"{display} was selected because the full-suite safety fallback "
            "was triggered."
        )
        lines = [headline, "", "Reason:"]
        if explanation.fallback_reason is not None:
            lines.append(f"  {explanation.fallback_reason}")
        return "\n".join(lines)

    if explanation.self_changed:
        return f"{display} was selected because it is itself changed."

    lines = [display]
    indent = "    "
    for path in explanation.path[1:]:
        lines.append(f"{indent}imports {path.as_posix()}")
        indent += "    "

    lines.append("")
    lines.append("Changed module:")
    if explanation.changed_path is not None:
        lines.append(f"    {explanation.changed_path.as_posix()}")

    return "\n".join(lines)
