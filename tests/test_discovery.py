import subprocess
from pathlib import Path

from impacttest.config import Config
from impacttest.discovery import discover_modules, find_repo_root


def _touch(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _as_dict(modules) -> dict[str, tuple[str, bool]]:
    return {m.path.as_posix(): (m.name, m.is_test) for m in modules}


def test_src_layout_discovery(tmp_path: Path) -> None:
    _touch(tmp_path / "src/app/__init__.py")
    _touch(tmp_path / "src/app/auth.py")
    _touch(tmp_path / "src/app/payments.py")
    _touch(tmp_path / "tests/test_auth.py")
    _touch(tmp_path / "tests/helpers.py")  # not a test file

    cfg = Config(source_roots=["src"], test_roots=["tests"])
    result = _as_dict(discover_modules(tmp_path, cfg))

    assert result == {
        "src/app/__init__.py": ("app", False),
        "src/app/auth.py": ("app.auth", False),
        "src/app/payments.py": ("app.payments", False),
        "tests/test_auth.py": ("tests.test_auth", True),
        "tests/helpers.py": ("tests.helpers", False),
    }


def test_flat_layout_discovery(tmp_path: Path) -> None:
    _touch(tmp_path / "shop/__init__.py")
    _touch(tmp_path / "shop/cart.py")
    _touch(tmp_path / "tests/test_cart.py")

    cfg = Config(source_roots=["."], test_roots=["tests"])
    result = _as_dict(discover_modules(tmp_path, cfg))

    assert result == {
        "shop/__init__.py": ("shop", False),
        "shop/cart.py": ("shop.cart", False),
        "tests/test_cart.py": ("tests.test_cart", True),
    }


def test_ignore_globs_exclude_matching_paths(tmp_path: Path) -> None:
    _touch(tmp_path / "app.py")
    _touch(tmp_path / ".venv/lib/somepkg.py")

    cfg = Config(source_roots=["."], test_roots=["tests"], ignore=[".venv/**"])
    result = _as_dict(discover_modules(tmp_path, cfg))

    assert result == {"app.py": ("app", False)}


def test_alternate_test_naming_convention(tmp_path: Path) -> None:
    _touch(tmp_path / "tests/auth_test.py")

    cfg = Config(source_roots=["."], test_roots=["tests"])
    result = _as_dict(discover_modules(tmp_path, cfg))

    assert result["tests/auth_test.py"] == ("tests.auth_test", True)


def test_test_named_file_outside_test_root_is_not_a_test(tmp_path: Path) -> None:
    _touch(tmp_path / "shop/test_utils.py")

    cfg = Config(source_roots=["."], test_roots=["tests"])
    result = _as_dict(discover_modules(tmp_path, cfg))

    assert result["shop/test_utils.py"] == ("shop.test_utils", False)


def test_find_repo_root_locates_a_real_git_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    nested = tmp_path / "src" / "app"
    nested.mkdir(parents=True)

    assert find_repo_root(nested) == tmp_path.resolve()
