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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
