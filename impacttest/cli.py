"""Command-line entry point (spec §7)."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from impacttest.analyzer import analyze_file, extract_imports
from impacttest.config import Config, load_config
from impacttest.discovery import discover_modules, find_repo_root, is_test_file
from impacttest.git import (
    WORKING_TREE,
    get_changed_files,
    get_diff_start,
    read_file_at_revision,
)
from impacttest.graph import build_graph
from impacttest.impact import compute_impact
from impacttest.models import ChangeSet, Module
from impacttest.resolver import build_index, module_name_for_path, resolve_all
from impacttest.runner import run_selected_tests


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="impacttest",
        description="Static test-impact analyzer for Python + pytest.",
    )
    subparsers = parser.add_subparsers(dest="command")

    analyze_parser = subparsers.add_parser(
        "analyze", help="Analyze changes without running tests."
    )
    _add_analysis_arguments(analyze_parser)

    run_parser = subparsers.add_parser(
        "run", help="Run only the tests affected by changes."
    )
    _add_analysis_arguments(run_parser)

    subparsers.add_parser("explain", help="Explain why a test was selected.")
    return parser


def _add_analysis_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base", default=None, help="Base revision to diff against (default: main)."
    )
    parser.add_argument(
        "--head",
        default=WORKING_TREE,
        help="Revision to diff to (default: the working tree).",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Where to start looking for the repository root (default: cwd).",
    )
    parser.add_argument(
        "--tests",
        action="append",
        default=None,
        help="Test root, overriding config. Repeat for multiple roots.",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=None,
        help="Source root, overriding config. Repeat for multiple roots.",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Print additional analysis detail."
    )


@dataclass
class AnalysisReport:
    """Everything ``analyze``'s output needs, already reduced to what a
    human -- or ``--json`` in Phase 13 -- would want to show.
    """

    changed_paths: list[Path] = field(default_factory=list)
    selected_tests: list[Path] = field(default_factory=list)
    total_tests: int = 0
    node_count: int = 0
    edge_count: int = 0
    uncertain: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)


def run_analysis(
    repo_root: Path, config: Config, base: str, head: str = WORKING_TREE
) -> AnalysisReport:
    """Wire discovery -> analyze -> resolve -> graph -> git diff -> impact.

    The graph is built from the *current* tree, since that's what
    discovery can see -- but a deleted (or renamed-away) module has
    nothing there to analyze, and skipping it would silently lose any
    test that still depends on it (spec §10). So its last content is read
    back out of history and folded in as an extra node before resolving,
    letting a leftover ``import deleted_module`` in a file that survived
    the change resolve to a real edge instead of vanishing.
    """
    modules = discover_modules(repo_root, config)
    for module in modules:
        result = analyze_file(repo_root / module.path)
        module.imports = result.imports
        module.uncertain = result.uncertain or result.failed

    total_tests = sum(1 for module in modules if module.is_test)
    changes = get_changed_files(repo_root, base=base, head=head)

    all_modules = list(modules)
    known_names = {module.name for module in modules}
    gone_paths = [*changes.deleted, *(old for old, _new in changes.renamed)]
    if gone_paths:
        diff_start = get_diff_start(repo_root, base=base, head=head)
        for path in gone_paths:
            ghost = _build_ghost_module(
                repo_root, diff_start, path, config, known_names
            )
            if ghost is not None:
                all_modules.append(ghost)
                known_names.add(ghost.name)

    index = build_index(all_modules)
    resolved = resolve_all(all_modules, index)
    graph = build_graph(resolved)

    changed_names = _changed_module_names(changes, config)
    impact_result = compute_impact(changed_names, graph, resolved)

    current_paths = {module.name: module.path for module in modules}
    selected_tests = sorted(
        (current_paths[name] for name in impact_result.tests if name in current_paths),
        key=lambda p: p.as_posix(),
    )
    changed_paths = sorted(
        {
            *changes.added,
            *changes.modified,
            *changes.deleted,
            *(p for pair in changes.renamed for p in pair),
        },
        key=lambda p: p.as_posix(),
    )
    uncertain = sorted(
        (
            (resolved_module.name, resolved_module.reasons)
            for resolved_module in resolved
            if resolved_module.uncertain and resolved_module.name in current_paths
        ),
        key=lambda item: item[0],
    )

    return AnalysisReport(
        changed_paths=changed_paths,
        selected_tests=selected_tests,
        total_tests=total_tests,
        node_count=graph.node_count,
        edge_count=graph.edge_count,
        uncertain=uncertain,
    )


def _build_ghost_module(
    repo_root: Path,
    diff_start: str,
    path: Path,
    config: Config,
    known_names: set[str],
) -> Module | None:
    """Reconstruct a Module for a path that no longer exists in the
    current tree, from its content at ``diff_start``. None if the path
    can't be named (outside every configured root) or its name is already
    claimed by a module discovery did find -- that module is the real one
    and this ghost would only confuse the index.
    """
    name = module_name_for_path(path, config)
    if name is None or name in known_names:
        return None

    source = read_file_at_revision(repo_root, diff_start, path)
    if source is None:
        return None

    try:
        imports, uncertain = extract_imports(source)
    except SyntaxError:
        return Module(path=path, name=name, uncertain=True)

    return Module(
        path=path,
        name=name,
        is_test=is_test_file(path, config.test_roots),
        imports=imports,
        uncertain=uncertain,
    )


def _changed_module_names(changes: ChangeSet, config: Config) -> set[str]:
    paths = [*changes.added, *changes.modified, *changes.deleted]
    paths.extend(p for pair in changes.renamed for p in pair)
    names = (module_name_for_path(path, config) for path in paths)
    return {name for name in names if name is not None}


def format_report(report: AnalysisReport, verbose: bool = False) -> str:
    """Render an AnalysisReport as the human-readable output spec §22
    describes.
    """
    title = "ImpactTest Analysis"
    lines = [title, "-" * len(title), ""]

    lines.append("Changed:")
    lines.extend(f"  {p.as_posix()}" for p in report.changed_paths)
    if not report.changed_paths:
        lines.append("  (none)")
    lines.append("")

    lines.append("Affected tests:")
    lines.extend(f"  {p.as_posix()}" for p in report.selected_tests)
    if not report.selected_tests:
        lines.append("  (none)")
    lines.append("")

    selected = len(report.selected_tests)
    lines.append(f"{selected} / {report.total_tests} test files selected")
    if report.total_tests > 0:
        reduction = (1 - selected / report.total_tests) * 100
        lines.append(f"{reduction:.1f}% test-file reduction")

    if verbose:
        lines.append("")
        lines.append(
            f"Modules analyzed: {report.node_count} (edges: {report.edge_count})"
        )
        if report.uncertain:
            lines.append("Uncertain modules:")
            for name, reasons in report.uncertain:
                lines.append(f"  {name}")
                lines.extend(f"    - {reason}" for reason in reasons)

    return "\n".join(lines)


def _prepare(args: argparse.Namespace) -> tuple[Path, Config, str]:
    """Resolve the repo root and config shared by ``analyze`` and ``run``,
    applying any ``--source``/``--tests``/``--base`` overrides. Raises
    RuntimeError (from ``find_repo_root``) if ``--root`` isn't inside a Git
    repository -- callers turn that into a clean error message.
    """
    repo_root = find_repo_root(Path(args.root))
    config = load_config(repo_root)
    if args.source:
        config.source_roots = args.source
    if args.tests:
        config.test_roots = args.tests
    base = args.base or config.base_branch
    return repo_root, config, base


def _cmd_analyze(args: argparse.Namespace) -> int:
    try:
        repo_root, config, base = _prepare(args)
        report = run_analysis(repo_root, config, base=base, head=args.head)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(format_report(report, verbose=args.verbose))
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        repo_root, config, base = _prepare(args)
        report = run_analysis(repo_root, config, base=base, head=args.head)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return run_selected_tests(repo_root, report.selected_tests)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "analyze":
        return _cmd_analyze(args)
    if args.command == "run":
        return _cmd_run(args)

    print(f"impacttest {args.command}: not implemented yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
