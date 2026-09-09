def test_package_imports():
    import impacttest  # noqa: F401


def test_cli_help_does_not_crash():
    from impacttest.cli import build_parser

    parser = build_parser()
    assert parser.prog == "impacttest"
