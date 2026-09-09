"""AST parsing and raw import extraction (Phase 2, spec §11)."""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from impacttest.models import RawImport


class _ImportVisitor(ast.NodeVisitor):
    """Collects raw import statements found anywhere in a module.

    Only visit_Import and visit_ImportFrom are overridden here. Every
    other node type -- FunctionDef, ClassDef, If, Try, With, and so on --
    falls through to ast.NodeVisitor's default generic_visit, which
    descends into all of a node's children. That is what makes imports
    nested inside a function or class body get found with no extra code:
    never add an override for a container node type here without calling
    self.generic_visit(node) inside it, or nested imports will silently
    stop being seen (spec §24).

    Star imports (`from x import *`) need no special handling either:
    ast represents the `*` as a single alias named "*", so they already
    produce RawImport(module="x", names=("*",)) via the normal
    ImportFrom path -- exactly the "treat as a dependency on the whole
    module" behavior spec §24 asks for. Module-level granularity means
    we never needed to know which specific names were imported.

    Imports guarded by `if TYPE_CHECKING:` (or any other conditional)
    are included too, for the same reason nested imports are: If is
    just another node type we don't override, so generic_visit walks
    into its body regardless of the condition. Including a type-only
    import can only cause an unnecessary test run, never a missed one,
    so there is no reason to filter it out (spec §24).
    """

    def __init__(self) -> None:
        self.imports: list[RawImport] = []
        self.uncertain = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(RawImport(module=alias.name, level=0))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = tuple(alias.name for alias in node.names)
        self.imports.append(RawImport(module=node.module, level=node.level, names=names))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if _is_dynamic_import_call(node):
            self.uncertain = True
        self.generic_visit(node)


_DYNAMIC_IMPORT_NAMES = {"__import__", "exec", "eval"}


def _is_dynamic_import_call(node: ast.Call) -> bool:
    """True for calls to importlib.import_module, __import__, exec, or
    eval -- the constructs spec §15 calls out as making static analysis
    unreliable, since what they actually do can depend on a runtime
    value we cannot see.

    Not exhaustive by design (e.g. `from importlib import import_module`
    then calling it bare isn't caught here). The conservative fallback
    in Phase 10 -- escalating any uncertain module to a full-suite run --
    is the real safety net for spellings this misses, not an ever-growing
    list of special cases here.
    """
    func = node.func
    if isinstance(func, ast.Name) and func.id in _DYNAMIC_IMPORT_NAMES:
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id == "importlib"
    )


def extract_imports(source: str) -> tuple[list[RawImport], bool]:
    """Parse ``source`` and return (raw imports, uncertain).

    Pure text in, data out -- no filesystem access, and ast.parse never
    executes the code, so this is safe to run against arbitrary
    repository sources.
    """
    tree = ast.parse(source)
    visitor = _ImportVisitor()
    visitor.visit(tree)
    return visitor.imports, visitor.uncertain


@dataclass
class AnalysisResult:
    """The result of analyzing one file on disk: its raw imports, or a
    failure marker.
    """

    imports: list[RawImport] = field(default_factory=list)
    uncertain: bool = False
    failed: bool = False
    error: str | None = None


def analyze_file(path: Path) -> AnalysisResult:
    """Read and parse one file. Never raises: an unreadable file or a
    syntax error becomes a failure marker (failed=True, uncertain=True)
    rather than an empty import list. An empty list would look
    identical to "this file genuinely has no imports" and silently
    under-select every test downstream that depends on it -- exactly
    the false-negative failure mode this tool exists to avoid
    (spec §23 Req 6).
    """
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        return AnalysisResult(uncertain=True, failed=True, error=str(exc))

    try:
        imports, uncertain = extract_imports(source)
    except SyntaxError as exc:
        return AnalysisResult(uncertain=True, failed=True, error=str(exc))

    return AnalysisResult(imports=imports, uncertain=uncertain)
