# Limitations

ImpactTest decides which tests to run by reading imports statically — it never
executes the code it analyzes. That is what makes it fast and safe to run
anywhere, and it is also a hard boundary on what can be known. This file records
what falls outside that boundary.

Everything here is either a deliberate design choice or a case the tool detects
and reports rather than guesses at. The one thing it must never do is quietly
miss a test, so where the analysis runs out of certainty it says so.

**Status.** The analysis core is implemented: repository discovery, AST import
extraction, module resolution, the dependency graph, impact traversal, Git
change detection, and `impacttest analyze`. Conservative fallbacks, `run`, and
`explain` are not built yet. Items below that describe fallback behavior are
marked *(planned)* and state the intended policy, not current behavior.

---

## What static analysis cannot see

### Dynamic imports

```python
importlib.import_module(name)  # name is a runtime value
__import__(module_name)
exec(source)
eval(expression)
```

There is no static answer here — what gets imported may depend on an
environment variable or a config file that does not exist until the program
runs. Modules containing any of these constructs are flagged `uncertain`, and
their dependency lists are treated as possibly incomplete *(planned: an
uncertain module anywhere in the impacted set escalates to a full-suite run)*.

Detection is by name and is not exhaustive. `from importlib import
import_module` followed by a bare `import_module(...)` call is not currently
caught. The escalation policy, not an ever-growing list of spellings, is the
real safety net here.

**Measured:** 12 of 99 files (12%) across `_pytest`, `pluggy` and `attr` trip
this flag — 11 of them in `_pytest` alone (14% of that package). Plugin
frameworks are unusually dynamic; ordinary application code is far lower. If
your repository looks like `_pytest`, expect the uncertainty policy rather than
the graph to dominate the selection.

### Runtime `sys.path` manipulation

Code that edits `sys.path`, installs import hooks, or relies on an editable
install pointing somewhere unexpected is not modeled. `sys.path` is a runtime
value; the `source_roots` setting is an explicit, static stand-in for it.

### Dependencies that are not imports

Nothing that connects a test to code without an import statement is visible:

- monkey patching and runtime attribute injection
- pytest fixtures reaching code through `conftest.py`
- plugins loaded via entry points
- test data files, fixtures on disk, database schemas
- subprocess and CLI invocations
- reflection over strings (`getattr(module, name)`)

*(Planned: a changed `conftest.py`, `pytest.ini`, or configured global file
forces a full-suite run, which covers the fixture case structurally rather than
by understanding it.)*

---

## Deliberate approximations

### Module-level granularity

The unit of change is a file, not a function. Touching one line of a module
selects every test that transitively imports it, even where the changed
function is not reachable from any of them. Function-level analysis is a
different and much larger project (spec §33 Expansion B).

### Re-exports resolve through the package, not to the definition

```python
# app/__init__.py
from .models import User

# tests/test_user.py
from app import User
```

The test gets an edge to `app`, not to `app.models` — nothing in the statement
says where `User` came from. The chain still holds transitively
(`tests.test_user → app → app.models`), so a change to `app/models.py` does
reach the test. Symbol-level resolution is not attempted.

### Conditional imports are always taken

Imports under `if TYPE_CHECKING:`, platform checks, or feature flags are
included regardless of whether that branch would run. A type-only import can
cause a test to run unnecessarily; excluding it could cause one to be skipped.

### Packages are dependencies of their contents

`from app.users import User` produces edges to both `app.users` and `app` —
reaching a submodule executes its package initializers first, so a change to
`app/__init__.py` can genuinely break the importer. This makes changes to
package initializers select widely. That is correct, and it is why
`__init__.py` files are worth keeping thin.

### Namespace packages are partially supported

For a PEP 420 package (a directory with no `__init__.py`), `from pkg import mod`
resolves correctly to `pkg.mod`. A bare `import pkg` does not resolve, because
`pkg` names no file — it is dropped as though external. In practice a bare
import of a namespace package with no submodule gives the importer nothing to
depend on, but the asymmetry is real.

---

## Where the resolver reports uncertainty

These are detected, not silently dropped:

| Case | Example | Result |
|---|---|---|
| Relative import above the source root | `from ....x import y` in `app/auth.py` | uncertain, no edge |
| Relative import resolving to nothing | `from .missing import x` | uncertain, keeps package edge |
| Internal-looking module with no file | `import app.missing` where `app` is ours | uncertain, keeps package edge |
| One name claimed by two files | two source roots both holding `app/auth.py` | uncertain, edge still emitted |

A relative import can only ever name something inside the repository, so one
that fails to resolve means the analysis lost track — never that the target is
third-party.

---

## Configuration assumptions

`source_roots` must match the repository layout. This is not validated, and
getting it wrong fails quietly: with `source_roots = ["."]` on a `src/` layout,
`src/app/auth.py` is named `src.app.auth`, so `from app.auth import login`
matches nothing, looks exactly like a third-party import, and is dropped. Every
internal edge disappears and the tool reports that almost nothing is affected.

Check `impacttest stats` against your file count before trusting a selection
*(planned — spec §6.4)*. Better diagnostics for misconfigured roots are Phase 13
work.

Where roots nest (`["src", "."]`), a file is named by the deeper root, because
that is the one a src layout puts on `sys.path`.

---

## Git change detection

### Filenames with quote-worthy characters are not unescaped

Git always renders a path in `diff --name-status` output verbatim, except
when the path contains a character it considers unsafe to print raw (a
literal tab, newline, backslash, or double quote), in which case the whole
path is wrapped in double quotes with those characters backslash-escaped.
`core.quotepath=false` (set on every invocation) only suppresses quoting for
non-ASCII bytes — it has no effect on this case. Git change detection does
not detect or unescape this quoting, so such a path would be parsed as a
literal string containing backslashes and quote characters rather than the
real filename, and would fail to resolve to anything.

This is believed to affect zero real files in practice: the characters that
trigger it are already invalid in a filename on Windows, and vanishingly rare
in filenames on any platform. Unescaping it properly is a small, well-defined
addition if it ever turns out to matter.

### Copies are not detected

`git diff --name-status` does not report copies (status `C`) unless run with
`-C`, which is not passed. A copied file that Git could have paired with its
source is instead reported as two independent changes — the source as
unchanged (invisible to the diff) and the copy as `A` (added) — which is the
same outcome as treating a genuine new file as added. No information is lost;
the copy relationship just isn't surfaced.

---

## Recovering deleted and renamed-away modules

Discovery only ever sees the current tree, so a module deleted (or renamed
away, which `analyze` treats the same way) has no on-disk file to analyze.
Left alone, that means a surviving file that still says `import
deleted_module` -- a real bug, and exactly the case spec §10 requires this
tool to catch -- would find nothing in the module index for that name and
just look like any other unresolved internal import.

`analyze` handles this by re-reading the deleted (or renamed-away) path's
content from Git history, at the commit the diff started from, and adding it
to the module graph as an extra node purely so other files' imports have
something to resolve to. If that recovery fails for any reason -- the blob
can't be read, the historical source doesn't parse -- the import isn't
silently dropped; it falls back to the resolver's ordinary "internal-looking
name, no file provides it" uncertainty (see below), which is the same
conservative outcome the resolver already gives an import it cannot place.

The recovered node's own dependencies are not used for anything -- only its
name being present in the index matters. If that name is also claimed by a
file discovery *did* find (the rare case of a deleted module's dotted name
colliding with an unrelated current one), the real file wins and no ghost
node is added, so this can never introduce a false edge, only occasionally
miss recovering one.

---

## Unreadable and unparseable files

A file that cannot be read, or that fails `ast.parse` (Python 2 source, syntax
newer than the running interpreter, genuine breakage), is marked
analysis-failed — never skipped. A skipped file would have no edges, so nothing
would depend on it, and its dependents would silently go unselected: the exact
failure this tool exists to prevent.

---

## How the numbers here were measured

The uncertainty rates come from running discovery, extraction and resolution
over the installed sources of `_pytest`, `pluggy` and `attr` with
`source_roots = ["."]`, counting modules flagged uncertain. That run produced
549 internal edges across 99 files with zero resolution-level uncertainty —
every uncertain module was flagged for a dynamic-import construct, not for an
import the resolver failed to resolve.
