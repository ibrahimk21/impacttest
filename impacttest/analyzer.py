"""AST parsing and raw import extraction (Phase 2, spec §11)."""
from __future__ import annotations

import ast

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
