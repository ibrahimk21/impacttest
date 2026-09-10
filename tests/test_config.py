from pathlib import Path

from impacttest.config import Config, load_config


def test_missing_pyproject_falls_back_to_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg == Config()


def test_missing_tool_impacttest_table_falls_back_to_defaults(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "unrelated"\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg == Config()


def test_partial_config_defaults_only_missing_keys(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.impacttest]\nsource_roots = ["lib"]\n', encoding="utf-8"
    )
    cfg = load_config(tmp_path)
    assert cfg.source_roots == ["lib"]
    assert cfg.test_roots == Config().test_roots
    assert cfg.base_branch == Config().base_branch
    assert cfg.ignore == Config().ignore
    assert cfg.global_files == Config().global_files


def test_full_explicit_config_is_respected(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[tool.impacttest]
source_roots = ["lib"]
test_roots = ["spec"]
base_branch = "develop"
ignore = ["vendor/**"]
global_files = ["shared/fixtures.py"]
""",
        encoding="utf-8",
    )
    cfg = load_config(tmp_path)
    assert cfg == Config(
        source_roots=["lib"],
        test_roots=["spec"],
        base_branch="develop",
        ignore=["vendor/**"],
        global_files=["shared/fixtures.py"],
    )


def test_global_files_defaults_to_empty_list() -> None:
    assert Config().global_files == []
