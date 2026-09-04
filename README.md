# repo-onboarding-brief

A [Rote](https://www.modiqo.ai) Play that briefs you on an unfamiliar repository — and **checks the
setup instructions instead of trusting them**.

```sh
rote play run pugarhuda/repo-onboarding-brief repo=https://github.com/pallets/click
```

## Why this exists

The expensive failure when you open an unfamiliar project is not *"I could not find the docs."*
It is *"I followed the docs, and command three did not exist."*

Every other repo-orientation tool summarises the README. That produces a setup guide nobody has
checked. This one resolves each documented command against the project's real definitions —
`package.json` scripts, `Makefile` targets, `justfile` recipes — and against the tools on your
machine, then reports which of the two failed:

- **works** — the project really defines it
- **tool missing** — it is real, but you lack the tool it needs
- **NOT DEFINED** — the README says to run it and the project never defines it (stale docs)
- **unresolved** — a plain command that cannot be checked either way

Those need opposite responses from a reader, so they are never merged into one status.

## What it reports

1. What this is — quoted from the manifest or the README, never guessed
2. Stack — ecosystems, manifests, lockfiles, version floors, dependency counts
3. How to run it — the command check above
4. Entry points
5. Layout map
6. Risk flags — no lockfile, no tests, no CI, committed secret-shaped files, no license
7. **What is unclear** — an explicit list of what the brief could not determine

Section 7 is the point. A shallow clone cannot know a contributor count, so the brief says so
instead of printing a `1` that reads like a fact.

## Effects

Read-only. No credentials, no adapters, no writes.

A local path is inspected in place. A URL is shallow-cloned into a temp directory, over `https://`,
`ssh://`, or `git@host:owner/name` only — anything else is refused rather than handed to `git`.
**Nothing the repository ships is ever executed**, which is the whole reason this is safe to point
at a repo you have not read yet.

## Structure

| Path | Role |
|---|---|
| `main.ts` | Play contract (frontmatter DAG) + presentation layer |
| `deps.toml` | Declared tools: `python3`, `git`, `sh` |
| `resources/resolve.sh` | Turns `repo` into a directory path; the trust boundary |
| `resources/probe.py` | Structure, manifests, risk facts |
| `resources/claims.py` | README command claims vs. real definitions |
| `tests/` | Self-checks, stdlib only |

The DAG is `resolve → (probe ‖ claims)`. The two analysis steps depend only on `resolve`, so they
run in parallel and either can fail without taking the other's findings down.

## Tests

```sh
python3 tests/test_probe.py
python3 tests/test_claims.py
```

Nine checks, no framework, no fixtures. Two of them are regressions for bugs found while building
this, both of which made the brief state something false:

- A `--depth 1` clone reported `commit_count: 1`. That is a shallow-clone artefact, not history.
- A justfile recipe with default parameters (`harness harness="codex":`) was not recognised, so
  three real recipes were reported as stale documentation.

Both were the same class of error — the tool confidently asserting something it had no basis for —
which is exactly what a Play whose value is honesty cannot afford to ship.

## Requirements

Python 3.11+ (uses `tomllib` for `pyproject.toml`), `git`, and a POSIX shell.
