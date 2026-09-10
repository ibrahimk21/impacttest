# ImpactTest

ImpactTest is a Python test-impact analyzer: it reads a Git diff, works out
which pytest files could be affected by the change through static import
analysis, and runs only those.

It never executes the code it analyzes. Everything it knows comes from
`ast` and `git`.

> **Status:** the analysis core is complete and tested end to end — discovery,
> import extraction, module resolution, dependency graph, impact traversal, Git
> change detection, `analyze` / `run` / `explain`, and the conservative
> full-suite fallback. Caching and the benchmark are next; this README carries
> no performance claims until they are measured.

## Install

```bash
pip install -e ".[dev]"
```

## Usage

```bash
impacttest analyze --base main    # what would run, and why
impacttest run --base main        # run exactly those tests
impacttest explain tests/test_login.py --base main
```

`--base` defaults to the configured base branch (`main`). By default the diff
runs from the merge-base of that branch to the **working tree**, so uncommitted
edits count as changes; `--head <rev>` compares a fixed revision instead.

## Demo

Real output, from this repository. `a4e8798` is the commit that added
`impacttest/impact.py`:

```text
$ impacttest analyze --base a4e8798^ --head a4e8798
ImpactTest Analysis
-------------------

Changed:
  impacttest/impact.py

Affected tests:
  tests/test_cli.py
  tests/test_explain.py
  tests/test_impact.py
  tests/test_integration.py
  tests/test_smoke.py

5 / 13 test files selected
61.5% test-file reduction
```

And why any one of those was picked:

```text
$ impacttest explain tests/test_explain.py --base a4e8798^ --head a4e8798
tests/test_explain.py
    imports impacttest/impact.py

Changed module:
    impacttest/impact.py
```

`tests/test_smoke.py` is in that list because it imports `impacttest.cli`
inside a function body, and `cli` imports `impact` — imports nested in
functions are found too, not just module-level ones.

## Architecture

Data flows one way. Each module owns one stage.

```text
discovery ──> analyzer ──> resolver ──> graph ──> impact ──> runner
   (files)     (imports)    (edges)   (adjacency)  (BFS)    (pytest)
                                                     ^
                        git (changed paths) ─────────┘
```

- **discovery** walks the configured source and test roots and maps each file
  to a dotted module name.
- **analyzer** extracts raw import statements with `ast`, including imports
  nested inside functions and classes, and flags constructs whose behavior is
  unknowable statically (`importlib.import_module`, `__import__`, `exec`,
  `eval`).
- **resolver** turns those statements into internal module names, handling
  absolute and relative imports and the submodule-versus-attribute ambiguity
  in `from package import name`. Anything that does not resolve to a file in
  the repository is third-party, and dropped.
- **graph** builds the forward adjacency and inverts it.
- **impact** runs a BFS over the reverse graph from the changed modules, with
  a visited set, so import cycles terminate. It keeps parent pointers, which
  is what `explain` walks back to render a path.
- **runner** invokes pytest on the selection and returns its exit code
  unchanged.

Graph nodes are dotted module names, never file paths. Paths appear only at
the boundaries — Git reports paths, pytest takes paths — and are converted
there.

## Safety philosophy

**False positives are free; false negatives are fatal.** Running a test that
did not need to run costs seconds. Failing to run one that did makes every
future selection untrustworthy.

So where the analysis runs out of certainty, it says so rather than guessing.
A file that cannot be read or parsed is marked analysis-failed, never skipped
— a skipped file has no edges, so nothing appears to depend on it, and its
dependents would silently go unselected. Uncertainty from any source escalates
to running the whole suite, with the reason printed:

```text
Full-suite safety fallback triggered.
Reason: tests/conftest.py changed
```

The triggers are a changed `conftest.py`, `pytest.ini` or `pyproject.toml`,
any file listed in `global_files`, an analysis failure anywhere in the
repository, and an uncertain module anywhere in the impacted set. The last of
those can be turned off with `--no-all-on-uncertain` if you would rather have
the tighter selection.

## Configuration

Optional. A repository with no configuration at all is a supported case; every
key falls back to a default.

```toml
[tool.impacttest]
source_roots = ["src"]
test_roots = ["tests"]
base_branch = "main"
ignore = [".venv/**", "venv/**", "build/**", "dist/**"]
global_files = []          # extra paths that force a full-suite run
```

## Limitations

Static analysis has a hard boundary, and `LIMITATIONS.md` documents where this
tool hits it: dynamic imports, `sys.path` manipulation at runtime, dependencies
that are not imports (monkey patching, fixtures reached through plugins, data
files), and the resolver cases that are deliberately reported as uncertain
rather than guessed. Every number in that file was measured, with the
measurement written down next to it.

## Development

```bash
python -m pytest      # full suite
ruff check .          # lint
ruff format .         # format
mypy .                # type check
```

The integration tests in `tests/test_integration.py` build real Git
repositories in a temp directory, commit a baseline, mutate the working tree
and drive the CLI against them — including one test that compares
`impacttest run` against a plain `pytest` run on the same repository and
requires them to agree.
