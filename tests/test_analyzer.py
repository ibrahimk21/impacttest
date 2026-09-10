from pathlib import Path

import pytest

from impacttest.analyzer import analyze_file, extract_imports
from impacttest.models import RawImport


def test_plain_import():
    imports, _ = extract_imports("import os")
    assert imports == [RawImport(module="os", level=0)]


def test_aliased_import_drops_the_alias():
    imports, _ = extract_imports("import shop.pricing as p")
    assert imports == [RawImport(module="shop.pricing", level=0)]


def test_multiple_names_in_one_import_statement():
    imports, _ = extract_imports("import os, sys")
    assert imports == [
        RawImport(module="os", level=0),
        RawImport(module="sys", level=0),
    ]


def test_from_import():
    imports, _ = extract_imports("from shop.pricing import apply_discount")
    assert imports == [
        RawImport(module="shop.pricing", level=0, names=("apply_discount",))
    ]


def test_relative_import_level_1():
    imports, _ = extract_imports("from .config import API_KEY")
    assert imports == [RawImport(module="config", level=1, names=("API_KEY",))]


def test_relative_import_level_2():
    imports, _ = extract_imports("from ..utils import helper")
    assert imports == [RawImport(module="utils", level=2, names=("helper",))]


def test_bare_relative_import_has_no_module():
    imports, _ = extract_imports("from . import config")
    assert imports == [RawImport(module=None, level=1, names=("config",))]


def test_star_import():
    imports, _ = extract_imports("from app.utils import *")
    assert imports == [RawImport(module="app.utils", level=0, names=("*",))]


def test_import_nested_inside_a_function():
    imports, _ = extract_imports(
        """
def load():
    import shop.plugins
"""
    )
    assert imports == [RawImport(module="shop.plugins", level=0)]


def test_import_nested_inside_a_method():
    imports, _ = extract_imports(
        """
class Loader:
    def load(self):
        from shop.utils import helper
"""
    )
    assert imports == [RawImport(module="shop.utils", level=0, names=("helper",))]


def test_conditional_type_checking_import_is_included():
    imports, _ = extract_imports(
        """
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import User
"""
    )
    assert RawImport(module="app.models", level=0, names=("User",)) in imports


def test_importlib_import_module_sets_uncertain():
    _, uncertain = extract_imports("importlib.import_module(name)")
    assert uncertain is True


def test_bare_dunder_import_sets_uncertain():
    _, uncertain = extract_imports("__import__(name)")
    assert uncertain is True


def test_exec_sets_uncertain():
    _, uncertain = extract_imports("exec(code)")
    assert uncertain is True


def test_eval_sets_uncertain():
    _, uncertain = extract_imports("eval(expr)")
    assert uncertain is True


def test_normal_file_is_not_uncertain():
    _, uncertain = extract_imports(
        """
import os
from shop.pricing import apply_discount

def total(x):
    return x * 2
"""
    )
    assert uncertain is False


def test_unrelated_import_module_method_does_not_false_trigger():
    # only a call shaped exactly like importlib.import_module(...) should
    # set uncertain -- an unrelated object with a same-named method must not.
    _, uncertain = extract_imports("catalog.import_module(name)")
    assert uncertain is False


def test_extract_imports_raises_on_syntax_error():
    # the pure, low-level function propagates -- analyze_file (below) is
    # the layer responsible for catching it.
    with pytest.raises(SyntaxError):
        extract_imports("def f(:\n    pass\n")


def test_analyze_file_marks_syntax_error_as_failed(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def f(:\n    pass\n", encoding="utf-8")

    result = analyze_file(broken)

    assert result.failed is True
    assert result.uncertain is True
    assert result.error is not None
    assert result.imports == []


def test_analyze_file_marks_unreadable_file_as_failed(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.py"

    result = analyze_file(missing)

    assert result.failed is True
    assert result.uncertain is True
    assert result.error is not None
    assert result.imports == []


def test_analyze_file_succeeds_on_a_normal_file(tmp_path: Path) -> None:
    ok = tmp_path / "ok.py"
    ok.write_text("import os\n", encoding="utf-8")

    result = analyze_file(ok)

    assert result.failed is False
    assert result.uncertain is False
    assert result.error is None
    assert result.imports == [RawImport(module="os", level=0)]
