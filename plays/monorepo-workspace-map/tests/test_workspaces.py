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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL PASS")
