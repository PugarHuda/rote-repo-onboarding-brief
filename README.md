# repo-onboarding-brief

[![repo-onboarding-brief](https://img.shields.io/badge/rote-repo--onboarding--brief-blue)](https://play.modiqo.ai/pugarhuda/repo-onboarding-brief)
[![monorepo-workspace-map](https://img.shields.io/badge/rote-monorepo--workspace--map-blue)](https://play.modiqo.ai/pugarhuda/monorepo-workspace-map)
[![codeowners-drift](https://img.shields.io/badge/rote-codeowners--drift-blue)](https://play.modiqo.ai/pugarhuda/codeowners-drift)
[![dependency-trust-diff](https://img.shields.io/badge/rote-dependency--trust--diff-blue)](https://play.modiqo.ai/pugarhuda/dependency-trust-diff)

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

## Where every claim comes from, and what the code needs that nobody wrote down

Since 0.4.0 the brief reads `README`, `CONTRIBUTING`, `DEVELOPMENT` and `docs/*.md`, and cites
`file:Lnn` for every command it checks. It also scans the source for environment variables the
code reads (`process.env.X`, `os.environ["X"]`, `os.getenv`, `os.Getenv`, `env::var`, Ruby `ENV`)
and compares them with `.env.example`, compose files and the docs. The ones read with **no
fallback and documented nowhere** are listed first: those are the variables the process dies on
during a stranger's first run. The brief then ends with **FIRST RUN, IN ORDER** — toolchain, the
install command the committed lockfile implies (`npm ci`, `pnpm install --frozen-lockfile`,
`uv sync`, `cargo build`, …), env, run, test — so it reads as a sequence, not a list.

## Taken from the neighbours, then finished

Four things the closest registry Plays each do one of; the brief now does all four in one run:

- **local-only files** (`stranger-test`'s idea): files in your checkout that git does not track,
  `.env` included, named as a stranger trap because a fresh clone will not have them
- **contradicting floors** (`first-run-reality`'s idea): a `.nvmrc` pin that the `engines` range
  rejects, so the reader cannot satisfy both
- **lockfile conflict** (`clone-ready`'s idea): two Node lockfiles committed, decided by
  `packageManager` or by nobody
- **broken doc links** (`readme-rot`'s idea): relative links and images in README, CONTRIBUTING
  and docs/ that point at files the tree does not have, with file and line

## Your machine vs the floors the project declares

Since 0.3.0 the brief compares what the repository demands with what you actually have: `engines`,
`.nvmrc`/`.node-version`, `packageManager`, `requires-python`, `.python-version`, the `go.mod` go
directive, `rust-toolchain` and `.tool-versions`, against the `--version` of your own `node`,
`python3`, `go` and `cargo`. Those are host tools, never the project's code. A Node two majors too
old is reported before any command below it is trusted.

## Requirements

Python 3.11+ (uses `tomllib` for `pyproject.toml`), `git`, and a POSIX shell.

## Browser QA of the published pages

`qa/play-pages.spec.mjs` opens the four public Play pages in real Chromium (Playwright) and asserts
HTTP 200, the version this repository declares, the Public badge, the declared tools, the inputs a
stranger is asked for, and the read-only wording. `cd qa && npm install && npx playwright install
chromium && npm test`.

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
does not descend. **GitLab files** (`.gitlab/CODEOWNERS` or `[Section]` headers) are evaluated
the GitLab way: sections independent, `^[Optional]` sections and `[Section][n]` approval counts
read, ownerless rules inheriting the section default, and files owned only through an optional
section counted separately. Syntax the forge rejects is flagged.

`verify_owners=true` asks `api.github.com` anonymously whether each `@user` and `@org` exists and
reports missing handles. Team membership is only visible to an authenticated member, so it is
never claimed; that and branch protection stay under **NOT CHECKED** rather than assumed fine.

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
`BEHIND`, `FLAGGED` or `UNCHECKED` when the registry did not answer. A fetch failure is never
reported as "same". Flags:

- `PUBLISHER_CHANGED`, `MAINTAINERS_REPLACED`, `LICENSE_CHANGED`, `LATEST_DEPRECATED`
- `INSTALL_SCRIPT_ADDED` — the newer version gains a `preinstall`/`install`/`postinstall` hook,
  code that runs on `npm install`; the shape of every recent npm worm
- `PROVENANCE_DROPPED` — the version you have carries a Sigstore build attestation and the newer
  one does not, so the publish path changed

What it says it cannot know: a publisher change is the account that ran `npm publish`, so a
handover to a CI token looks identical to a takeover. On `axios/axios` the first run flagged three
packages, and two of them had moved to a "GitHub Actions" publishing account, which is exactly the
case the output tells you to look at rather than assume. It reads registry metadata, never tarballs.
pnpm, yarn and bun lockfiles are named as unsupported rather than silently skipped.

Reads `package-lock.json`, `npm-shrinkwrap.json`, `pnpm-lock.yaml` (v5 to v9) and `yarn.lock`
(classic and berry), so vue, vite, next and babel are in scope, not just npm projects. Its only
network access is anonymous GETs to `registry.npmjs.org` and one POST to `api.osv.dev`. Source under
`plays/dependency-trust-diff/`; the self-check runs fully offline against fixtures.

---

# How these differ from the Plays next to them

The registry has ~1,000 Plays. These are the closest neighbours and the one thing each of ours
does that they do not.

| Ours | Closest neighbours | What only ours does |
|---|---|---|
| repo-onboarding-brief | `chaitanyagidwani/repo-onboarding-brief`, `documentation-contract-referee`, `readme-rot`, `first-run-reality`, `stranger-test`, `clone-ready` | Resolves each README command against **this machine's PATH** as well as the manifests, so "stale docs" (`NOT DEFINED`) and "you lack the tool" (`tool missing`) are separate verdicts; covers 12 ecosystems (npm/pnpm/yarn/bun scripts, Make, just, Cargo bins, `go run` paths, pyproject scripts, tox, nox, Taskfile, compose services, Dockerfile, `npx` deps); ends with an explicit **WHAT IS UNCLEAR** list |
| monorepo-workspace-map | `dep-skew` (version skew only), `repo-dependency-graph` (module imports, not packages) | Package-level boundaries with **cycles**, **version skew**, **internal version mismatch** (a sibling range the workspace copy cannot satisfy, so the manager silently pulls from the registry) and **unlisted packages** no glob covers, across npm/pnpm/Cargo/go.work/uv/lerna |
| codeowners-drift | none audit CODEOWNERS; `reviewer-finder` and `bus-factor` answer who *should* own code | Rules that match nothing, rules **shadowed** by a later rule (last match wins), files with no owner, GitHub *and* GitLab semantics, optional `verify_owners` against api.github.com |
| dependency-trust-diff | `package-abandonment-signal`, `upstream-pulse`, `pkg-xray` (health of the latest version), `npm-scripts-audit` (hooks already installed) | Diffs the version you **pinned** against `latest` on the trust axes a version bump hides: publisher, maintainers, license, **install hook added**, **provenance dropped** |

Shared rules that no neighbour states as plainly: read-only, stdlib only, nothing the target
repository ships is ever executed, a fetch failure is never reported as "fine", and every analyzer
stays under rote's 64 KB stdout capture so a large input degrades to a counted omission instead of
a silent "could not be audited".
