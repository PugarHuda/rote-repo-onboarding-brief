#!/usr/bin/env python3
"""Self-check for coverage.py. Run: python3 test_coverage.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.parent / "resources"


def run(root):
    p = subprocess.run([sys.executable, str(HERE / "coverage.py"), str(root)], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def touch(root, *paths, text="x"):
    for p in paths:
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)


def test_uncovered_stale_disabled_and_globs():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "package.json", "packages/a/package.json", "packages/b/package.json",
              "api/requirements.txt", "Dockerfile", ".github/workflows/ci.yml", "infra/main.tf")
        touch(r, "tools/pyproject.toml", text="[tool.ruff]\nline-length = 100\n")  # tool-only, not a pip manifest
        (r / ".github" / "dependabot.yml").write_text("""version: 2
updates:
  - package-ecosystem: "npm"
    directory: "/"
    schedule:
      interval: weekly
  - package-ecosystem: npm
    directories:
      - "/packages/*"
    schedule:
      interval: daily
    open-pull-requests-limit: 0
  - package-ecosystem: gomod          # nothing here uses Go
    directory: /
    schedule:
      interval: monthly
  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
""")
        d = run(r)
        assert d["config"] == ".github/dependabot.yml" and d["entries"] == 4
        cov = {(c["ecosystem"], c["directory"]): c.get("interval") for c in d["covered"]}
        assert cov == {("npm", "/"): "weekly", ("github-actions", "/"): "weekly"}, cov
        unc = sorted((u["ecosystem"], u["directory"]) for u in d["uncovered"])
        assert unc == [("docker", "/"), ("pip", "/api"), ("terraform", "/infra")], unc
        dis = sorted((x["ecosystem"], x["directory"]) for x in d["disabled"])
        assert dis == [("npm", "/packages/a"), ("npm", "/packages/b")], dis
        assert [s["ecosystem"] for s in d["stale_entries"]] == ["gomod"]
        assert d["ecosystems_present"] == 7 and d["coverage_pct"] == 28.6
        assert not any(u["directory"] == "/tools" for u in d["uncovered"])  # tool-only pyproject ignored


def test_no_config_and_renovate():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "package.json", "go.mod")
        d = run(r)
        assert d["config"] is None and d["renovate"] is None and d["uncovered_count"] == 2 and d["coverage_pct"] == 0.0
        (r / "renovate.json").write_text('{\n  // json5 comment\n  "extends": ["config:recommended"],\n  "enabledManagers": ["npm"],\n}\n')
        d = run(r)
        assert d["renovate"]["readable"] and d["renovate"]["enabled_managers"] == ["npm"]
        assert [c["ecosystem"] for c in d["covered"]] == ["npm"] and d["uncovered"][0]["ecosystem"] == "gomod"
        assert "enabledManagers" in d["uncovered"][0]["why"]


def test_four_space_indentation_and_tabs_parse_too():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "go.mod", ".github/workflows/ci.yml")
        (r / ".github" / "dependabot.yml").write_text(
            "version: 2\nupdates:\n"
            "    - package-ecosystem: gomod\n      directory: \"/\"\n      schedule:\n          interval: weekly\n"
            "    - package-ecosystem: github-actions\n      directory: /\n      schedule:\n          interval: monthly\n")
        d = run(r)
        assert d["entries"] == 2 and d["coverage_pct"] == 100.0, d
        # cli/cli's shape: items at column 0, nested ignore lists, cooldown, groups
        (r / ".github" / "dependabot.yml").write_text(
            "version: 2\nupdates:\n- package-ecosystem: gomod\n  directory: \"/\"\n  schedule:\n    interval: \"daily\"\n"
            "  cooldown:\n    default-days: 3\n  ignore:\n  - dependency-name: \"*\"\n    update-types:\n    - version-update:semver-major\n"
            "- package-ecosystem: \"github-actions\"\n  directory: \"/\"\n  schedule:\n      interval: \"daily\"\n  groups:\n    codeql-actions:\n      patterns:\n        - \"github/codeql-action/*\"\n")
        d = run(r)
        assert d["entries"] == 2 and d["coverage_pct"] == 100.0, d
        assert {c["interval"] for c in d["covered"]} == {"daily"}


def test_renovate_ignore_paths_exclude_directories():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "package.json", "examples/demo/package.json", "packages/ui/package.json")
        (r / "renovate.json").write_text(json.dumps({"ignorePaths": ["**/examples/**"]}))
        d = run(r)
        cov = sorted(c["directory"] for c in d["covered"]); unc = {u["directory"]: u["why"] for u in d["uncovered"]}
        assert cov == ["/", "/packages/ui"], cov
        assert "/examples/demo" in unc and "ignorePaths" in unc["/examples/demo"], unc



def test_wildcard_ignore_mutes_an_entry_but_a_narrowed_one_does_not():
    """`dependency-name: "*"` with nothing narrowing it drops every version update.

    The same wildcard narrowed by update-types is the ordinary "no majors" rule and
    must stay in COVERED, or the finding is noise nobody reads twice.
    """
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "package.json", "api/requirements.txt")
        (r / ".github").mkdir(exist_ok=True)
        (r / ".github" / "dependabot.yml").write_text("""version: 2
updates:
  - package-ecosystem: npm
    directory: "/"
    schedule:
      interval: weekly
    ignore:
      - dependency-name: "*"
  - package-ecosystem: pip
    directory: "/api"
    schedule:
      interval: weekly
    ignore:
      - dependency-name: "*"
        update-types: ["version-update:semver-major"]
      - dependency-name: "boto3"
""")
        d = run(r)
        muted = {m["directory"]: m["why"] for m in d["muted"]}
        assert list(muted) == ["/"], d["muted"]
        assert "every version update is dropped" in muted["/"], muted
        assert [c["directory"] for c in d["covered"]] == ["/api"], d["covered"]


def test_registry_named_but_never_declared_is_a_config_problem():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "package.json")
        (r / ".github").mkdir(exist_ok=True)
        (r / ".github" / "dependabot.yml").write_text("""version: 2
registries:
  npm-internal:
    type: npm-registry
    url: https://npm.pkg.github.com
updates:
  - package-ecosystem: npm
    directory: "/"
    registries:
      - npm-internal
      - npm-typo
    schedule:
      interval: weekly
""")
        d = run(r)
        why = " ".join(p["why"] for p in d["problems"])
        assert "npm-typo" in why and "npm-internal" not in why, d["problems"]


def test_double_star_directories_and_two_more_ecosystems():
    """`/packages/**` is the glob monorepos write; .gitmodules and compose files are
    ecosystems Dependabot supports and almost nobody adds an entry for."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "packages/a/package.json", "packages/deep/nested/package.json",
              ".gitmodules", "docker-compose.yml")
        (r / ".github").mkdir(exist_ok=True)
        (r / ".github" / "dependabot.yml").write_text("""version: 2
updates:
  - package-ecosystem: npm
    directories:
      - "/packages/**"
    schedule:
      interval: weekly
""")
        d = run(r)
        cov = sorted(c["directory"] for c in d["covered"])
        assert cov == ["/packages/a", "/packages/deep/nested"], cov
        unc = sorted(u["ecosystem"] for u in d["uncovered"])
        assert unc == ["docker-compose", "gitsubmodule"], unc



def test_real_json5_and_urls_survive_the_comment_stripper():
    """Regression, found on babel, vuejs/core and home-assistant: all three renovate
    configs read as unparseable, so the play reported a fully covered repository as
    0% covered. Two causes — a regex comment stripper ate the // in an https:// URL,
    and .json5 really is json5: bare keys and single-quoted strings."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "package.json", "packages/ui/package.json", "tests/e2e/package.json")
        (r / ".github").mkdir(exist_ok=True)
        (r / ".github" / "renovate.json5").write_text("""{
  // the URL below is the whole bug: its // is not a comment
  $schema: 'https://docs.renovatebot.com/renovate-schema.json',
  extends: ['config:recommended'],
  ignorePaths: ['**/tests/**'],
  packageRules: [
    { matchDepTypes: ['peerDependencies'], enabled: false },
  ],
}
""")
        d = run(r)
        assert d["renovate"]["readable"] is True, d["renovate"]
        assert d["renovate"]["ignore_paths"] == ["**/tests/**"], d["renovate"]
        cov = sorted(c["directory"] for c in d["covered"])
        assert cov == ["/", "/packages/ui"], cov
        assert [u["directory"] for u in d["uncovered"]] == ["/tests/e2e"], d["uncovered"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
