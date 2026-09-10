from pathlib import Path

from impacttest.config import Config
from impacttest.discovery import discover_modules
from impacttest.models import Module, RawImport
from impacttest.resolver import (
    ModuleIndex,
    build_index,
    module_name_for_path,
    resolve_import,
)


def _module(path: str, name: str, *, imports=(), is_test=False, uncertain=False) -> Module:
    return Module(
        path=Path(path),
        name=name,
        is_test=is_test,
        imports=list(imports),
        uncertain=uncertain,
    )


def _index(*specs: tuple[str, str]) -> ModuleIndex:
    """Build an index from (path, module name) pairs."""
    return build_index([_module(path, name) for path, name in specs])


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# --- path -> module name -------------------------------------------------


def test_src_layout_strips_the_source_root():
    cfg = Config(source_roots=["src"], test_roots=["tests"])

    assert module_name_for_path(Path("src/app/auth.py"), cfg) == "app.auth"
    assert module_name_for_path(Path("src/app/__init__.py"), cfg) == "app"


def test_flat_layout_keeps_the_full_path():
    cfg = Config(source_roots=["."], test_roots=["tests"])

    assert module_name_for_path(Path("shop/cart.py"), cfg) == "shop.cart"
    assert module_name_for_path(Path("shop/__init__.py"), cfg) == "shop"


def test_most_specific_source_root_wins_regardless_of_order():
    # A path under both roots must get the same name either way round --
    # "app.auth" is what a src layout makes importable, and an edge only
    # exists if both ends spell the name identically.
    for roots in ([".", "src"], ["src", "."]):
        cfg = Config(source_roots=roots, test_roots=["tests"])
        assert module_name_for_path(Path("src/app/auth.py"), cfg) == "app.auth"


def test_test_root_paths_are_named_from_the_repo_root():
    cfg = Config(source_roots=["src"], test_roots=["tests"])

    assert module_name_for_path(Path("tests/test_cart.py"), cfg) == "tests.test_cart"
    assert module_name_for_path(Path("tests/helpers.py"), cfg) == "tests.helpers"


def test_paths_outside_every_root_have_no_module_name():
    cfg = Config(source_roots=["src"], test_roots=["tests"])

    assert module_name_for_path(Path("docs/conf.py"), cfg) is None
    assert module_name_for_path(Path("src/app/data.json"), cfg) is None


def test_naming_agrees_with_discovery_on_a_real_tree(tmp_path: Path):
    # module_name_for_path is used for paths that no longer exist (a file
    # git reports as deleted has no discovered Module). If it disagreed
    # with discovery for the paths that do exist, a deleted file would map
    # to a name no graph node carries and its dependents would go
    # unselected -- so pin the two together.
    _touch(tmp_path / "src/app/__init__.py")
    _touch(tmp_path / "src/app/auth.py")
    _touch(tmp_path / "tests/test_auth.py")
    _touch(tmp_path / "tests/helpers.py")
    cfg = Config(source_roots=["src"], test_roots=["tests"])

    for module in discover_modules(tmp_path, cfg):
        assert module_name_for_path(module.path, cfg) == module.name


# --- the index -----------------------------------------------------------


def test_index_maps_names_and_paths_including_packages():
    index = _index(
        ("src/app/__init__.py", "app"),
        ("src/app/auth.py", "app.auth"),
    )

    assert index.by_name == {
        "app": Path("src/app/__init__.py"),
        "app.auth": Path("src/app/auth.py"),
    }
    assert index.by_path[Path("src/app/__init__.py")] == "app"
    assert "app" in index and "app.missing" not in index
    assert index.ambiguous == set()


def test_imports_resolve_under_both_layouts():
    src_layout = _index(
        ("src/app/__init__.py", "app"),
        ("src/app/users.py", "app.users"),
    )
    flat_layout = _index(
        ("shop/__init__.py", "shop"),
        ("shop/cart.py", "shop.cart"),
    )

    importer = _module("src/app/auth.py", "app.auth")
    resolved = resolve_import(RawImport("app.users", 0, ("User",)), importer, src_layout)
    assert resolved.modules == ("app", "app.users")

    importer = _module("tests/test_cart.py", "tests.test_cart", is_test=True)
    resolved = resolve_import(RawImport("shop.cart", 0, ("Cart",)), importer, flat_layout)
    assert resolved.modules == ("shop", "shop.cart")
