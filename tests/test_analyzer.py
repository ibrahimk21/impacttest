from impacttest.analyzer import extract_imports
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
    assert imports == [
        RawImport(module="shop.utils", level=0, names=("helper",))
    ]


def test_conditional_type_checking_import_is_included():
    imports, _ = extract_imports(
        """
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import User
"""
    )
    assert RawImport(module="app.models", level=0, names=("User",)) in imports
