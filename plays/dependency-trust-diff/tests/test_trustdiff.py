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
        assert d["ok"] is False and "could not be read" in d["reason"]
        (r / "pnpm-lock.yaml").unlink()
        d = run(r, fx)
        assert d["ok"] is False and "nothing is pinned" in d["reason"]


def test_pnpm_and_yarn_lockfiles_are_read():
    with tempfile.TemporaryDirectory() as t:
        r, fx = Path(t) / "repo", Path(t) / "fx"
        r.mkdir(); fx.mkdir()
        (r / "package.json").write_text(json.dumps({"dependencies": {"vue": "^3.4"}, "devDependencies": {"vitest": "^1"}}))
        (r / "pnpm-lock.yaml").write_text("""lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      vue:
        specifier: ^3.4
        version: 3.4.21(typescript@5.4.5)
    devDependencies:
      vitest:
        specifier: ^1
        version: 1.6.0

packages:

  '@babel/code-frame@7.24.2':
    resolution: {integrity: sha512-x}

  vue@3.4.21:
    resolution: {integrity: sha512-y}

  vitest@1.6.0:
    resolution: {integrity: sha512-z}
""")
        for n, v in (("vue", "3.4.21"), ("vitest", "1.6.0")):
            fixture(fx, n, v, publisher="p", license="MIT", maintainers=["p"])
            fixture(fx, n, "latest", version=v, publisher="p", license="MIT", maintainers=["p"])
        (fx / "osv.json").write_text("{}")
        d = run(r, fx)
        assert d["ok"] and d["lockfile"] == "pnpm-lock.yaml"
        assert d["pinned_total"] == 3 and sorted(d["current"]) == ["vitest", "vue"], d
        # v5 shape
        (r / "pnpm-lock.yaml").write_text("lockfileVersion: 5.4\n\nspecifiers:\n  vue: ^3.4\n\ndependencies:\n  vue: 3.4.21\n\npackages:\n\n  /vue/3.4.21:\n    resolution: {integrity: x}\n\n  /@babel/core/7.24.0:\n    resolution: {integrity: y}\n")
        d = run(r, fx)
        assert d["pinned_total"] == 2 and d["checked"] == 1  # direct set falls back to package.json; vitest is not in this lock
        # yarn classic and berry
        (r / "pnpm-lock.yaml").unlink()
        (r / "yarn.lock").write_text('# yarn lockfile v1\n\n"@babel/core@^7.0.0", "@babel/core@^7.24.0":\n  version "7.24.0"\n\nvue@^3.4:\n  version "3.4.21"\n\nvitest@^1:\n  version "1.6.0"\n')
        d = run(r, fx)
        assert d["lockfile"] == "yarn.lock" and d["pinned_total"] == 3 and sorted(d["current"]) == ["vitest", "vue"], d
        (r / "yarn.lock").write_text('__metadata:\n  version: 8\n\n"@babel/core@npm:^7.24.0":\n  version: 7.24.0\n\n"vue@npm:^3.4":\n  version: 3.4.21\n\n"vitest@npm:^1":\n  version: 1.6.0\n')
        d = run(r, fx)
        assert d["pinned_total"] == 3 and sorted(d["current"]) == ["vitest", "vue"], d


def pypi_meta(version, license, requires_python=">=3.9", yanked=False, author="a"):
    return {"info": {"version": version, "license": license, "requires_python": requires_python, "yanked": yanked, "author": author, "classifiers": []}}


def test_python_lockfiles_go_to_pypi():
    with tempfile.TemporaryDirectory() as t:
        r, fx = Path(t) / "repo", Path(t) / "fx"
        r.mkdir(); fx.mkdir()
        (r / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "0.1"\ndependencies = ["requests>=2", "Flask_Login>=0.6"]\n')
        (r / "uv.lock").write_text('''version = 1
[[package]]
name = "app"
version = "0.1"
source = { editable = "." }
[package.metadata]
requires-dist = [{ name = "requests", specifier = ">=2" }, { name = "flask-login", specifier = ">=0.6" }]

[[package]]
name = "requests"
version = "2.31.0"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "flask-login"
version = "0.6.3"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "certifi"
version = "2024.2.2"
source = { registry = "https://pypi.org/simple" }
''')
        (fx / "pypi__requests@2.31.0.json").write_text(json.dumps(pypi_meta("2.31.0", "Apache 2.0", ">=3.7", yanked=True)))
        (fx / "pypi__requests@latest.json").write_text(json.dumps(pypi_meta("2.32.3", "Apache-2.0", ">=3.8")))
        (fx / "pypi__flask-login@0.6.3.json").write_text(json.dumps(pypi_meta("0.6.3", "MIT")))
        (fx / "pypi__flask-login@latest.json").write_text(json.dumps(pypi_meta("0.6.3", "MIT")))
        (fx / "osv.json").write_text(json.dumps({"requests@2.31.0": ["GHSA-9wx4-h78v-vm56"]}))
        d = run(r, fx)
        assert d["ok"] and d["lockfile"] == "uv.lock" and d["ecosystem"] == "PyPI"
        assert d["pinned_total"] == 3 and d["checked"] == 2   # certifi is transitive
        fl = {f["name"]: f["findings"] for f in d["flagged"]}
        assert fl["requests"] == ["KNOWN_VULNERABILITY", "LOCKED_YANKED", "REQUIRES_PYTHON_CHANGED", "LICENSE_CHANGED"], fl
        assert d["current"] == ["flask-login"]
        assert any("uploader" in n for n in d["not_checked"])
        # requirements.txt with == pins, no lock
        (r / "uv.lock").unlink(); (r / "pyproject.toml").unlink()
        (r / "requirements.txt").write_text("requests==2.31.0  # http\nFlask-Login[extra]==0.6.3\n-e .\n")
        d = run(r, fx)
        assert d["lockfile"] == "requirements.txt" and d["checked"] == 2
        # a Python project with nothing pinned says so
        (r / "requirements.txt").write_text("requests>=2\n"); (r / "pyproject.toml").write_text("[project]\nname='x'\n")
        d = run(r, fx)
        assert d["ok"] is False and "nothing is pinned" in d["reason"]


def crate_meta(num, login, license, yanked=False, rust_version=None, as_crate_doc=False):
    v = {"num": num, "published_by": {"login": login}, "license": license, "yanked": yanked, "rust_version": rust_version, "crate_size": 10000}
    return {"crate": {"max_stable_version": num}, "versions": [v]} if as_crate_doc else {"version": v}


def test_cargo_lock_goes_to_crates_io():
    with tempfile.TemporaryDirectory() as t:
        r, fx = Path(t) / "repo", Path(t) / "fx"
        r.mkdir(); fx.mkdir()
        (r / "Cargo.toml").write_text('[package]\nname = "app"\n[dependencies]\nserde = "1"\ntokio = { version = "1", features = ["full"] }\n')
        (r / "Cargo.lock").write_text('''version = 3

[[package]]
name = "app"
version = "0.1.0"

[[package]]
name = "serde"
version = "1.0.197"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "tokio"
version = "1.36.0"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "mio"
version = "0.8.11"
source = "registry+https://github.com/rust-lang/crates.io-index"
''')
        (fx / "crates__serde@1.0.197.json").write_text(json.dumps(crate_meta("1.0.197", "dtolnay", "MIT OR Apache-2.0", rust_version="1.56")))
        (fx / "crates__serde@latest.json").write_text(json.dumps(crate_meta("1.0.210", "someone-else", "MIT OR Apache-2.0", rust_version="1.61", as_crate_doc=True)))
        (fx / "crates__tokio@1.36.0.json").write_text(json.dumps(crate_meta("1.36.0", "carllerche", "MIT", yanked=True)))
        (fx / "crates__tokio@latest.json").write_text(json.dumps(crate_meta("1.36.0", "carllerche", "MIT", as_crate_doc=True)))
        (fx / "osv.json").write_text("{}")
        d = run(r, fx)
        assert d["ok"] and d["lockfile"] == "Cargo.lock" and d["ecosystem"] == "crates.io"
        assert d["pinned_total"] == 3 and d["checked"] == 2  # mio transitive, app is the root
        fl = {f["name"]: f["findings"] for f in d["flagged"]}
        assert fl["serde"] == ["MSRV_CHANGED", "PUBLISHER_CHANGED", "MAINTAINERS_REPLACED"], fl
        assert fl["tokio"] == ["LOCKED_YANKED"], fl


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
