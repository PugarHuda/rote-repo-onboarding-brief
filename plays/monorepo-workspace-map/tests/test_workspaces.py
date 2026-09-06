#!/usr/bin/env python3
"""Self-check for workspaces.py. Run: python3 test_workspaces.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.parent / "resources"


def ws(root):
    p = subprocess.run([sys.executable, str(HERE / "workspaces.py"), str(root)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def mk(root, path, pkg):
    d = root / path
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps(pkg))
    return d


def test_not_a_monorepo_says_so_and_says_what_it_checked():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text('{"name":"solo"}')
        d = ws(r)
        assert d["is_monorepo"] is False
        assert "pnpm-workspace.yaml" in d["reason"]


def test_npm_workspaces_edges_and_leaves():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps(
            {"name": "root", "private": True, "workspaces": ["packages/*"]}))
        mk(r, "packages/ui", {"name": "@acme/ui", "version": "1.0.0",
                              "dependencies": {"@acme/core": "1.0.0", "react": "^19"}})
        mk(r, "packages/core", {"name": "@acme/core", "version": "1.0.0",
                                "dependencies": {"react": "^19"}})
        d = ws(r)
        assert d["is_monorepo"] is True
        assert d["workspace_kind"] == "npm/yarn workspaces"
        assert d["member_count"] == 2, d["members"]
        assert ["@acme/ui", "@acme/core"] in d["internal_edges"]
        # core is depended on; ui is not -> ui is the leaf.
        assert d["leaf_packages"] == ["@acme/ui"], d["leaf_packages"]
        assert d["cycles"] == []


def test_version_skew_is_the_finding_that_matters():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["p/*"]}))
        mk(r, "p/a", {"name": "a", "dependencies": {"lodash": "^4.17.20"}})
        mk(r, "p/b", {"name": "b", "dependencies": {"lodash": "^3.0.0"}})
        mk(r, "p/c", {"name": "c", "dependencies": {"lodash": "^4.17.20"}})
        d = ws(r)
        skew = {s["dependency"]: s["versions"] for s in d["version_skew"]}
        assert "lodash" in skew, d["version_skew"]
        assert skew["lodash"]["b"] == "^3.0.0"
        assert len(skew["lodash"]) == 3


def test_cycles_are_detected():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["p/*"]}))
        mk(r, "p/a", {"name": "a", "dependencies": {"b": "1"}})
        mk(r, "p/b", {"name": "b", "dependencies": {"a": "1"}})
        d = ws(r)
        assert d["cycles"], d
        flat = {n for c in d["cycles"] for n in c}
        assert flat == {"a", "b"}, d["cycles"]


def test_member_without_manifest_is_reported_not_silently_dropped():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["p/*"]}))
        mk(r, "p/real", {"name": "real"})
        (r / "p" / "empty").mkdir(parents=True)
        d = ws(r)
        assert d["member_count"] == 1
        assert any(s["path"].endswith("empty") for s in d["skipped"]), d["skipped"]


def test_internal_version_mismatch_and_unlisted_packages():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["packages/*"]}))
        mk(r, "packages/core", {"name": "@acme/core", "version": "2.0.0"})
        mk(r, "packages/app", {"name": "@acme/app", "version": "1.0.0",
                                "dependencies": {"@acme/core": "^1.0.0"}})       # workspace has 2.0.0 -> registry fallback
        mk(r, "packages/ok", {"name": "@acme/ok", "version": "1.0.0",
                               "dependencies": {"@acme/core": "workspace:*"}})   # link, never a mismatch
        mk(r, "tools/forgotten", {"name": "@acme/forgotten", "version": "0.1.0"})  # not covered by any glob
        mk(r, "packages/core/sub-entry", {"name": "@acme/core/sub"})  # nested in a member: not unlisted
        d = ws(r)
        assert d["internal_version_mismatch"] == [
            {"package": "@acme/app", "depends_on": "@acme/core", "wants": "^1.0.0", "workspace_has": "2.0.0"}], d
        assert d["unlisted_packages"] == ["tools/forgotten"], d


def test_babel_sized_monorepo_stays_under_rotes_64kb_stdout_cap():
    # Regression: babel/babel (162 members, 761 edges) came to 76 KB pretty-printed and
    # rote's presentation read a truncated body as "could not be mapped".
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["packages/*"]}))
        for i in range(300):
            deps = {f"@big/pkg-{(i + k) % 300}": "workspace:*" for k in range(1, 6)}
            deps["lodash"] = "^4.0.0" if i % 2 else "^4.17.21"
            mk(r, f"packages/pkg-{i}", {"name": f"@big/pkg-{i}", "version": "1.0.0", "dependencies": deps})
        p = subprocess.run([sys.executable, str(HERE / "workspaces.py"), str(r)],
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stderr
        assert len(p.stdout.encode()) <= 65536, len(p.stdout)
        d = json.loads(p.stdout)
        assert d["member_count"] == 300 and d["edge_count"] == 1500
        assert len(d["members"]) + d["members_omitted"] == 300
        assert len(d["internal_edges"]) + d["edges_omitted"] == 1500
        assert d["version_skew"][0]["package_count"] == 300 and len(d["version_skew"][0]["versions"]) == 12


def test_turbo_pipeline_coverage():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"name": "root", "workspaces": ["apps/*", "packages/*"]}))
        (r / "turbo.json").write_text(json.dumps({"tasks": {"build": {"dependsOn": ["^build"]}, "test": {}, "//#root-only": {}}}))
        mk(r, "apps/web", {"name": "web", "scripts": {"build": "next build", "test": "vitest"}})
        mk(r, "packages/ui", {"name": "ui", "scripts": {"build": "tsup"}})
        mk(r, "packages/config", {"name": "config"})
        d = ws(r)
        tasks = {t["task"]: t for t in d["pipeline"]["tasks"]}
        assert set(tasks) == {"build", "test"}  # //#root-only is a root task, not a package task
        assert tasks["build"]["packages_without"] == ["config"]
        assert tasks["test"]["packages_without"] == ["config", "ui"] and tasks["test"]["packages_with_script"] == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL PASS")
