"""AST parsing and raw import extraction (Phase 2, spec §11)."""
from __future__ import annotations

import ast

from impacttest.models import RawImport


class _ImportVisitor(ast.NodeVisitor):
    """Collects raw import statements found anywhere in a module."""

    def __init__(self) -> None:
        self.imports: list[RawImport] = []
        self.uncertain = False

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(RawImport(module=alias.name, level=0))
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
