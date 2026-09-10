"""Command-line entry point (spec §7)."""

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="impacttest",
        description="Static test-impact analyzer for Python + pytest.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("analyze", help="Analyze changes without running tests.")
    subparsers.add_parser("run", help="Run only the tests affected by changes.")
    subparsers.add_parser("explain", help="Explain why a test was selected.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    print(f"impacttest {args.command}: not implemented yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
