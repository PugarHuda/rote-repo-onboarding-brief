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

---

# Also in this repository

Two more Plays share the same resolver and the same rules: read-only, stdlib only, nothing the
repository ships is ever executed, and every "could not determine" is said out loud.

## monorepo-workspace-map

```sh
rote play run pugarhuda/monorepo-workspace-map repo=https://github.com/vuejs/core
```

Maps a monorepo's package boundaries: which workspace packages exist, which depend on which, which
are leaves nobody imports, and which dependency **cycles** exist. Also reports **version skew**,
the same external dependency pinned differently across packages, which installs cleanly and breaks
once at runtime. Understands npm/yarn, pnpm, Cargo, `go.work`, uv, and lerna workspaces. A
repository that is not a monorepo gets a stated answer and the list of definitions checked, never
an empty map. Source under `plays/monorepo-workspace-map/`.

## codeowners-drift

```sh
rote play run pugarhuda/codeowners-drift repo=https://github.com/hashicorp/terraform
```

Audits `CODEOWNERS` against the files the repository actually tracks. A CODEOWNERS file is written
once and the tree moves out from under it; the forge never says a rule stopped matching. Three
findings carry it:

- **matches nothing** — the path moved or never existed; the rule routes no review
- **shadowed** — a later rule wins for every file this one names, because the *last* match wins
- **without an owner** — every tracked file no rule covers, grouped by directory, with a coverage
  percentage

It reads the one file GitHub uses when several are present (`.github/`, root, `docs/`, in that
order) and says which. Matching follows GitHub's documented semantics, including that `docs/*`
does not descend. Syntax the forge rejects (negation, malformed handles, GitLab section headers)
is flagged. Whether a team exists and whether branch protection requires the review need the forge
API, so both are listed under **NOT CHECKED** rather than assumed fine.

Verified on `hashicorp/terraform` (3 rules pointing at provisioners that no longer exist) and
`home-assistant/core` (2,183 rules over 27,740 files, 90.1% covered, 2 stale). Source under
`plays/codeowners-drift/`; self-check in `plays/codeowners-drift/tests/`.

## dependency-trust-diff

```sh
rote play run pugarhuda/dependency-trust-diff repo=https://github.com/axios/axios
```

`npm outdated` tells you a newer version exists. It does not tell you that the newer version was
**published by a different account**, or under a **different license**. A package name survives a
maintainer handover, a sold project, and a takeover unchanged, so the name is not the trust anchor.

For every package the lockfile pins (direct dependencies by default, `scope=all` for the tree), it
asks the public npm registry for the locked version and for `latest`, and reports each as `CURRENT`,
`BEHIND`, `FLAGGED` (`PUBLISHER_CHANGED`, `LICENSE_CHANGED`, `MAINTAINERS_REPLACED`,
`LATEST_DEPRECATED`) or `UNCHECKED` when the registry did not answer. A fetch failure is never
reported as "same".

What it says it cannot know: a publisher change is the account that ran `npm publish`, so a
handover to a CI token looks identical to a takeover. On `axios/axios` the first run flagged three
packages, and two of them had moved to a "GitHub Actions" publishing account, which is exactly the
case the output tells you to look at rather than assume. It reads registry metadata, never tarballs.
pnpm, yarn and bun lockfiles are named as unsupported rather than silently skipped.

Its only network access is anonymous GETs to `registry.npmjs.org`. Source under
`plays/dependency-trust-diff/`; the self-check runs fully offline against fixtures.
