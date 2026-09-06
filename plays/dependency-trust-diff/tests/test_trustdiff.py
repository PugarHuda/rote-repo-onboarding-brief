#!/usr/bin/env python3
"""Self-check for trustdiff.py, fully offline via TRUSTDIFF_FIXTURE_DIR. Run: python3 test_trustdiff.py"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.parent / "resources"


def run(root, fixtures, *extra):
    env = dict(os.environ, TRUSTDIFF_FIXTURE_DIR=str(fixtures), TRUSTDIFF_OSV_FIXTURE=str(Path(fixtures) / "osv.json"))
    p = subprocess.run([sys.executable, str(HERE / "trustdiff.py"), str(root), *extra],
                       capture_output=True, text=True, timeout=60, env=env)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def meta(version, publisher, license, maintainers, deprecated=False, hooks=(), provenance=False, size=None):
    d = {"version": version, "_npmUser": {"name": publisher}, "license": license,
         "maintainers": [{"name": m} for m in maintainers],
         "scripts": {h: "node evil.js" for h in hooks}, "dist": {}}
    if size:
        d["dist"]["unpackedSize"] = size
    if provenance:
        d["dist"]["attestations"] = {"url": "https://registry.npmjs.org/-/npm/v1/attestations/x", "provenance": {"predicateType": "https://slsa.dev/provenance/v1"}}
    if deprecated:
        d["deprecated"] = "use something else"
    return d


def worm_row(d):
    return next(f for f in d["flagged"] if f["name"] == "worm")


def fixture(dirp, name, filever, version=None, **kw):
    # `filever` is what the registry URL asks for ("1.0.0" or "latest"); `version` is what it answers.
    (dirp / f"{name.replace('/', '__')}@{filever}.json").write_text(json.dumps(meta(version or filever, **kw)))


def test_v3_lockfile_direct_scope_and_findings():
    with tempfile.TemporaryDirectory() as t:
        r, fx = Path(t) / "repo", Path(t) / "fx"
        r.mkdir(); fx.mkdir()
        (r / "package-lock.json").write_text(json.dumps({
            "lockfileVersion": 3,
            "packages": {
                "": {"dependencies": {"left-pad": "^1.0.0", "@scope/pkg": "^2.0.0", "steady": "^1.0.0",
                                      "ghost": "^1.0.0", "old": "^1.0.0", "worm": "^3.0.0"}},
                "node_modules/left-pad": {"version": "1.0.0"},
                "node_modules/@scope/pkg": {"version": "2.0.0"},
                "node_modules/steady": {"version": "1.0.0"},
                "node_modules/ghost": {"version": "1.0.0"},
                "node_modules/old": {"version": "1.0.0"},
                "node_modules/worm": {"version": "3.0.0"},
                "node_modules/transitive": {"version": "9.9.9"},
                "node_modules/old/node_modules/nested": {"version": "0.1.0"},
            }}))
        # publisher changed + license changed
        fixture(fx, "left-pad", "1.0.0", publisher="alice", license="MIT", maintainers=["alice"])
        fixture(fx, "left-pad", "latest", version="1.3.0", publisher="mallory", license="SSPL-1.0", maintainers=["mallory"])
        # same publisher, newer version, license as object
        (fx / "@scope__pkg@2.0.0.json").write_text(json.dumps(
            {"version": "2.0.0", "_npmUser": {"name": "bob"}, "license": {"type": "Apache-2.0"},
             "maintainers": [{"name": "bob"}]}))
        fixture(fx, "@scope/pkg", "latest", version="2.1.0", publisher="bob", license="Apache-2.0", maintainers=["bob", "carol"])
        # current
        fixture(fx, "steady", "1.0.0", publisher="x", license="MIT", maintainers=["x"])
        fixture(fx, "steady", "latest", version="1.0.0", publisher="x", license="MIT", maintainers=["x"])
        # ghost: no fixture for latest -> UNCHECKED
        fixture(fx, "ghost", "1.0.0", publisher="x", license="MIT", maintainers=["x"])
        # worm: the newer version drops its build attestation and grows a postinstall hook
        fixture(fx, "worm", "3.0.0", publisher="w", license="MIT", maintainers=["w"], provenance=True, size=120_000)
        fixture(fx, "worm", "latest", version="3.0.1", publisher="w", license="MIT", maintainers=["w"], hooks=("postinstall",), size=900_000)
        # old: latest deprecated
        fixture(fx, "old", "1.0.0", publisher="x", license="MIT", maintainers=["x"])
        fixture(fx, "old", "latest", version="1.1.0", publisher="x", license="MIT", maintainers=["x"], deprecated=True)

        # OSV: the CURRENT package `steady` carries an advisory at exactly 1.0.0
        (fx / "osv.json").write_text(json.dumps({"steady@1.0.0": ["GHSA-xxxx-yyyy-zzzz", "CVE-2026-0001"]}))
        d = run(r, fx)
        assert d["ok"] and d["lockfile"] == "package-lock.json"
        assert d["osv"] == {"checked": True, "vulnerable_packages": 1, "note": None}
        steady = next(f for f in d["flagged"] if f["name"] == "steady")
        assert steady["findings"][0] == "KNOWN_VULNERABILITY" and steady["vulns"] == ["GHSA-xxxx-yyyy-zzzz", "CVE-2026-0001"]
        assert d["pinned_total"] == 8 and d["direct_total"] == 6 and d["checked"] == 6
        assert d["counts"] == {"BEHIND": 1, "FLAGGED": 4, "UNCHECKED": 1}
        flagged = {f["name"]: f["findings"] for f in d["flagged"]}
        assert flagged["left-pad"] == ["PUBLISHER_CHANGED", "LICENSE_CHANGED", "MAINTAINERS_REPLACED"]
        assert flagged["old"] == ["LATEST_DEPRECATED"]
        assert flagged["worm"] == ["INSTALL_SCRIPT_ADDED", "PROVENANCE_DROPPED", "SIZE_JUMP"]
        assert worm_row(d)["size_jump"] == {"from": 120_000, "to": 900_000, "factor": 7.5}
        worm = next(f for f in d["flagged"] if f["name"] == "worm")
        assert worm["install_scripts_added"] == ["postinstall"]
        assert d["behind"] == [{"name": "@scope/pkg", "locked": "2.0.0", "latest": "2.1.0"}]
        assert d["unchecked"][0]["name"] == "ghost" and "latest" in d["unchecked"][0]["why"]
        assert d["current"] == []  # steady moved to FLAGGED by the advisory

        # scope=all reaches the transitive package too, and max_packages truncates honestly
        d2 = run(r, fx, "scope=all", "max_packages=3")
        assert d2["checked"] == 3 and d2["skipped_over_max"] == 5


def test_non_npm_locks_are_named_not_faked():
    with tempfile.TemporaryDirectory() as t:
        r, fx = Path(t) / "repo", Path(t) / "fx"
        r.mkdir(); fx.mkdir()
        (r / "package.json").write_text("{}")
        (r / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
        d = run(r, fx)
        assert d["ok"] is False and "pnpm-lock.yaml" in d["reason"]
        (r / "pnpm-lock.yaml").unlink()
        d = run(r, fx)
        assert d["ok"] is False and "nothing is pinned" in d["reason"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
