#!/usr/bin/env python3
"""For every npm dependency a lockfile pins, compare the version you have with the
newest version on the public registry, and report the two things a version number
hides: the publishing account changed, or the license changed.

Input:  <repo-dir> [scope=direct|all] [max_packages=N]
Output: JSON

Network: GET https://registry.npmjs.org/<name>/<version> and /<name>/latest, read-only,
no credentials. A package that could not be fetched is UNCHECKED, never "same".
Set TRUSTDIFF_FIXTURE_DIR to a directory of <name>@<version>.json files to run offline.

Stdlib only. Executes nothing the repository ships.
"""
import json
import re
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REGISTRY = "https://registry.npmjs.org"
FIXTURE_DIR = os.environ.get("TRUSTDIFF_FIXTURE_DIR")
LOCKFILES = ["package-lock.json", "npm-shrinkwrap.json"]
OTHER_LOCKS = ["bun.lockb", "bun.lock"]


def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def locked_packages(root):
    """Return (lockfile, {name: version}, direct_names) or (None, ...) with a reason."""
    for lf in LOCKFILES:
        p = root / lf
        if not p.is_file():
            continue
        lock = load_json(p)
        if not isinstance(lock, dict):
            return lf, None, None, f"{lf} is not valid JSON"
        pinned, direct = {}, set()
        pkgs = lock.get("packages")
        if isinstance(pkgs, dict):  # lockfileVersion 2 or 3
            rootpkg = pkgs.get("", {}) or {}
            for k in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
                direct.update((rootpkg.get(k) or {}).keys())
            for key, meta in pkgs.items():
                if not key or not isinstance(meta, dict):
                    continue
                # nearest "node_modules/<name>" wins for the top-level copy
                name = key.rsplit("node_modules/", 1)[-1]
                if meta.get("link") or not meta.get("version"):
                    continue
                if "node_modules/" not in key:
                    continue  # workspace member, not a registry package
                if key.count("node_modules/") == 1 or name not in pinned:
                    pinned[name] = meta["version"]
        elif isinstance(lock.get("dependencies"), dict):  # lockfileVersion 1
            for name, meta in lock["dependencies"].items():
                if isinstance(meta, dict) and meta.get("version"):
                    pinned[name] = meta["version"]
            pj = load_json(root / "package.json") or {}
            for k in ("dependencies", "devDependencies", "optionalDependencies"):
                direct.update((pj.get(k) or {}).keys())
        else:
            return lf, None, None, f"{lf} has neither `packages` nor `dependencies`"
        return lf, pinned, direct, None
    if (root / "pnpm-lock.yaml").is_file():
        pinned, direct = read_pnpm_lock(Path(root / "pnpm-lock.yaml").read_text(encoding="utf-8", errors="replace"))
        if not direct:  # older lockfile without importers: fall back to package.json for the direct set
            pj = load_json(root / "package.json") or {}
            for k in ("dependencies", "devDependencies", "optionalDependencies"):
                direct.update((pj.get(k) or {}).keys())
        if pinned:
            return "pnpm-lock.yaml", pinned, direct, None
        return "pnpm-lock.yaml", None, None, "pnpm-lock.yaml could not be read (no packages section recognised)"
    if (root / "yarn.lock").is_file():
        pinned = read_yarn_lock(Path(root / "yarn.lock").read_text(encoding="utf-8", errors="replace"))
        pj = load_json(root / "package.json") or {}
        direct = set()
        for k in ("dependencies", "devDependencies", "optionalDependencies"):
            direct.update((pj.get(k) or {}).keys())
        if pinned:
            return "yarn.lock", pinned, direct, None
        return "yarn.lock", None, None, "yarn.lock could not be read (no version entries recognised)"
    others = [o for o in OTHER_LOCKS if (root / o).is_file()]
    if others:
        return None, None, None, (f"only {', '.join(others)} found; this Play reads package-lock.json, "
                                  "npm-shrinkwrap.json, pnpm-lock.yaml and yarn.lock")
    if (root / "package.json").is_file():
        return None, None, None, "package.json without a lockfile: nothing is pinned, so there is no locked version to compare"
    lf, pinned, direct = python_locked(root)
    if pinned:
        return lf, pinned, direct, None
    lf, pinned, direct = cargo_locked(root)
    if pinned:
        return lf, pinned, direct, None
    if (root / "Cargo.toml").is_file():
        return None, None, None, "Cargo.toml without a Cargo.lock: nothing is pinned to compare"
    if (root / "pyproject.toml").is_file() or (root / "setup.py").is_file():
        return None, None, None, "Python project without a lock (uv.lock, poetry.lock, Pipfile.lock) or a ==-pinned requirements.txt: nothing is pinned to compare"
    return None, None, None, "no package.json, pyproject.toml or Cargo.toml: not an npm, Python or Rust project"


PNPM_KEY = re.compile(r"^  ['\"]?/?(@?[^@'\"\s/]+(?:/[^@'\"\s/]+)?)@([^'\"(:\s]+)")   # v6/v9: name@1.2.3 or /name@1.2.3
PNPM_KEY_V5 = re.compile(r"^  /(@?[^/\s]+(?:/[^/\s]+)?)/(\d[^:_(\s]*)")               # v5: /name/1.2.3


def read_pnpm_lock(text):
    """{name: version} for every package pnpm pinned, and the root importer's direct names.
    A regex read of the two sections that matter; avoids a YAML dependency."""
    pinned, direct = {}, set()
    section, importer, dep_kind = None, None, None
    pending = None  # direct dependency awaiting its `version:` line
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            section = line.rstrip(":").strip()
            importer = None
            continue
        if section == "importers":
            if line.startswith("  ") and not line.startswith("   "):
                importer = line.strip().rstrip(":").strip("'\"")
                continue
            if importer in (".", "") and line.startswith("    ") and not line.startswith("     "):
                dep_kind = line.strip().rstrip(":")
                continue
            if importer in (".", "") and dep_kind in ("dependencies", "devDependencies", "optionalDependencies"):
                if line.startswith("      ") and not line.startswith("       ") and line.rstrip().endswith(":"):
                    pending = line.strip().rstrip(":").strip("'\"")
                    direct.add(pending)
                elif pending and line.strip().startswith("version:"):
                    v = line.split("version:", 1)[1].strip().strip("'\"").split("(")[0]
                    if v and v[0].isdigit():
                        pinned.setdefault(pending, v)
                    pending = None
        elif section == "packages":
            m = PNPM_KEY.match(line) or PNPM_KEY_V5.match(line)
            if m and line.rstrip().endswith(":"):
                name, ver = m.group(1), m.group(2).split("(")[0].split("_")[0]
                if ver and ver[0].isdigit():
                    pinned.setdefault(name, ver)
    return pinned, direct


YARN_HEADER = re.compile(r"^\S.*:$")


def read_yarn_lock(text):
    """{name: version} from yarn.lock, classic (`version "1.2.3"`) and berry (`version: 1.2.3`)."""
    pinned = {}
    names = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        if YARN_HEADER.match(line):
            names = []
            for key in line.rstrip(":").split(","):
                key = key.strip().strip("'\"")
                if key.startswith("__metadata"):
                    continue
                name = key.rsplit("@", 1)[0] if key.count("@") > (1 if key.startswith("@") else 0) else key
                if name:
                    names.append(name)
            continue
        if names and line.startswith("  ") and line.strip().startswith("version"):
            v = line.strip()[len("version"):].strip(" :").strip("'\"")
            if v and v[0].isdigit():
                for n in names:
                    pinned.setdefault(n, v)
            names = []
    return pinned


# ---------------------------------------------------------------------------
# Python: uv.lock, poetry.lock, Pipfile.lock, requirements.txt -> PyPI
# ---------------------------------------------------------------------------
def _toml(path):
    try:
        import tomllib
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _norm_py(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def python_locked(root):
    """(lockfile, {name: version}, direct_names) for a Python project, or (None, None, None)."""
    pj = _toml(root / "pyproject.toml") or {}
    direct = set()
    for spec in (pj.get("project") or {}).get("dependencies", []) or []:
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
        if m:
            direct.add(_norm_py(m.group(1)))
    for grp in ((pj.get("project") or {}).get("optional-dependencies") or {}).values():
        for spec in grp or []:
            m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
            if m:
                direct.add(_norm_py(m.group(1)))
    poetry = ((pj.get("tool") or {}).get("poetry") or {})
    for k in ("dependencies", "dev-dependencies"):
        direct.update(_norm_py(n) for n in (poetry.get(k) or {}) if n.lower() != "python")
    for grp in (poetry.get("group") or {}).values():
        direct.update(_norm_py(n) for n in ((grp or {}).get("dependencies") or {}))
    dev = ((pj.get("dependency-groups") or {}))
    for grp in dev.values():
        for spec in grp or []:
            if isinstance(spec, str):
                m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
                if m:
                    direct.add(_norm_py(m.group(1)))

    uv = _toml(root / "uv.lock") if (root / "uv.lock").is_file() else None
    if uv and isinstance(uv.get("package"), list):
        pinned = {}
        for pkg in uv["package"]:
            src = pkg.get("source") or {}
            if not isinstance(pkg, dict) or not pkg.get("name") or not pkg.get("version"):
                continue
            if src.get("editable") or src.get("virtual"):
                for d in (pkg.get("metadata") or {}).get("requires-dist", []) or []:
                    if isinstance(d, dict) and d.get("name"):
                        direct.add(_norm_py(d["name"]))
                continue
            if "registry" in src or not src:
                pinned[_norm_py(pkg["name"])] = str(pkg["version"])
        if pinned:
            return "uv.lock", pinned, direct
    po = _toml(root / "poetry.lock") if (root / "poetry.lock").is_file() else None
    if po and isinstance(po.get("package"), list):
        pinned = {_norm_py(x["name"]): str(x["version"]) for x in po["package"]
                  if isinstance(x, dict) and x.get("name") and x.get("version") and not (x.get("source") or {}).get("type") in ("git", "directory", "file", "url")}
        if pinned:
            return "poetry.lock", pinned, direct
    if (root / "Pipfile.lock").is_file():
        try:
            pl = json.loads((root / "Pipfile.lock").read_text(encoding="utf-8", errors="replace"))
            pinned = {}
            for sect in ("default", "develop"):
                for n, meta in (pl.get(sect) or {}).items():
                    v = (meta or {}).get("version", "")
                    if isinstance(v, str) and v.startswith("=="):
                        pinned[_norm_py(n)] = v[2:]
                        direct.add(_norm_py(n))
            if pinned:
                return "Pipfile.lock", pinned, direct
        except Exception:
            pass
    for req in ("requirements.txt", "requirements/base.txt", "requirements-dev.txt"):
        if (root / req).is_file():
            pinned = {}
            for line in (root / req).read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.split("#", 1)[0].strip()
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([0-9][^\s;]*)", line)
                if m:
                    pinned[_norm_py(m.group(1))] = m.group(2)
            if pinned:
                direct.update(pinned.keys())
                return req, pinned, direct
    return None, None, None


PYPI = "https://pypi.org/pypi"


def fetch_pypi(name, version):
    if FIXTURE_DIR:
        p = Path(FIXTURE_DIR) / f"pypi__{name}@{version}.json"
        return load_json(p) if p.is_file() else None
    url = f"{PYPI}/{urllib.parse.quote(name)}/json" if version == "latest" else f"{PYPI}/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "dependency-trust-diff/0.7 (rote play)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def summarize_pypi(meta):
    if not isinstance(meta, dict) or not isinstance(meta.get("info"), dict):
        return None
    info = meta["info"]
    lic = info.get("license_expression") or info.get("license")
    if not lic or len(str(lic)) > 60:
        cls = [c for c in (info.get("classifiers") or []) if c.startswith("License ::")]
        lic = cls[-1].split("::")[-1].strip() if cls else (lic[:60] + "…" if lic else None)
    people = [x for x in (info.get("author"), info.get("maintainer")) if x]
    return {
        "version": info.get("version"),
        "publisher": None,  # PyPI's JSON API does not expose who uploaded a release
        "license": str(lic).strip() if lic else None,
        "maintainers": people[:8],
        "deprecated": False,
        "yanked": bool(info.get("yanked")),
        "requires_python": info.get("requires_python"),
        "install_scripts": [],
        "provenance": None,
        "unpacked_size": None,
        "file_count": None,
    }


# ---------------------------------------------------------------------------
# Rust: Cargo.lock -> crates.io. Unlike PyPI, crates.io records who published
# each version, so PUBLISHER_CHANGED works here too.
# ---------------------------------------------------------------------------
CRATES = "https://crates.io/api/v1/crates"


def cargo_locked(root):
    lock = _toml(root / "Cargo.lock") if (root / "Cargo.lock").is_file() else None
    if not lock or not isinstance(lock.get("package"), list):
        return None, None, None
    pinned = {}
    for pkg in lock["package"]:
        if isinstance(pkg, dict) and pkg.get("name") and pkg.get("version") and "crates.io" in str(pkg.get("source", "")):
            pinned.setdefault(pkg["name"], str(pkg["version"]))
    manifest = _toml(root / "Cargo.toml") or {}
    direct = set()
    for sect in ("dependencies", "dev-dependencies", "build-dependencies"):
        direct.update((manifest.get(sect) or {}).keys())
        direct.update(((manifest.get("workspace") or {}).get(sect) or {}).keys())
    for tgt in (manifest.get("target") or {}).values():
        for sect in ("dependencies", "dev-dependencies", "build-dependencies"):
            direct.update(((tgt or {}).get(sect) or {}).keys())
    # workspace members' direct deps count as direct too
    for m in (manifest.get("workspace") or {}).get("members", []) or []:
        for d in root.glob(m):
            mt = _toml(d / "Cargo.toml") or {}
            for sect in ("dependencies", "dev-dependencies", "build-dependencies"):
                direct.update((mt.get(sect) or {}).keys())
    direct = {d.replace("_", "-") if d not in pinned and d.replace("_", "-") in pinned else d for d in direct}
    return ("Cargo.lock", pinned, direct) if pinned else (None, None, None)


def fetch_crate(name, version):
    if FIXTURE_DIR:
        p = Path(FIXTURE_DIR) / f"crates__{name}@{version}.json"
        return load_json(p) if p.is_file() else None
    url = f"{CRATES}/{urllib.parse.quote(name)}" if version == "latest" else f"{CRATES}/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}"
    req = urllib.request.Request(url, headers={"User-Agent": "dependency-trust-diff/0.7 (rote play; github.com/PugarHuda/rote-repo-onboarding-brief)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def summarize_crate(meta):
    if not isinstance(meta, dict):
        return None
    v = meta.get("version")
    if v is None and isinstance(meta.get("crate"), dict):
        # the crate document: pick the version marked max_stable_version (or max_version)
        want = meta["crate"].get("max_stable_version") or meta["crate"].get("max_version")
        v = next((x for x in meta.get("versions", []) if x.get("num") == want), None)
    if not isinstance(v, dict):
        return None
    pub = v.get("published_by") or {}
    return {
        "version": v.get("num"),
        "publisher": pub.get("login") if isinstance(pub, dict) else None,
        "license": v.get("license"),
        "maintainers": [pub.get("login")] if isinstance(pub, dict) and pub.get("login") else [],
        "deprecated": False,
        "yanked": bool(v.get("yanked")),
        "requires_python": None,
        "rust_version": v.get("rust_version"),
        "install_scripts": [],
        "provenance": None,
        "unpacked_size": v.get("crate_size") if isinstance(v.get("crate_size"), int) else None,
        "file_count": None,
    }


def norm_license(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v.strip() or None
    if isinstance(v, dict):
        return norm_license(v.get("type"))
    if isinstance(v, list):
        parts = [norm_license(x) for x in v]
        return " OR ".join(p for p in parts if p) or None
    return str(v)


def fetch_version(name, version):
    """Registry metadata for one version, or None on any failure."""
    if FIXTURE_DIR:
        p = Path(FIXTURE_DIR) / f"{name.replace('/', '__')}@{version}.json"
        return load_json(p) if p.is_file() else None
    url = f"{REGISTRY}/{urllib.parse.quote(name, safe='@')}/{urllib.parse.quote(version)}"
    req = urllib.request.Request(url, headers={"User-Agent": "dependency-trust-diff/0.1 (rote play)"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


INSTALL_HOOKS = ("preinstall", "install", "postinstall")


def summarize(meta):
    if not isinstance(meta, dict):
        return None
    user = meta.get("_npmUser") or {}
    scripts = meta.get("scripts") if isinstance(meta.get("scripts"), dict) else {}
    dist = meta.get("dist") if isinstance(meta.get("dist"), dict) else {}
    return {
        "version": meta.get("version"),
        "publisher": (user.get("name") if isinstance(user, dict) else None),
        "license": norm_license(meta.get("license") or meta.get("licenses")),
        # capped: rote keeps 64KB of stdout, and some packages list 40+ maintainers
        "maintainers": sorted(m.get("name") for m in (meta.get("maintainers") or [])
                              if isinstance(m, dict) and m.get("name"))[:8],
        "deprecated": bool(meta.get("deprecated")),
        # Lifecycle hooks that run arbitrary code on `npm install`. A hook that
        # appears in a newer version is the shape of every recent npm worm.
        "install_scripts": sorted(k for k in INSTALL_HOOKS if scripts.get(k)),
        "unpacked_size": dist.get("unpackedSize") if isinstance(dist.get("unpackedSize"), int) else None,
        "file_count": dist.get("fileCount") if isinstance(dist.get("fileCount"), int) else None,
        # npm provenance: a Sigstore attestation linking the tarball to the CI run
        # that built it. Present, then absent, means the publish path changed.
        "provenance": bool(dist.get("attestations")),
    }


def check(name, locked_version, ecosystem="npm"):
    if ecosystem == "PyPI":
        have = summarize_pypi(fetch_pypi(name, locked_version))
        latest = summarize_pypi(fetch_pypi(name, "latest"))
    elif ecosystem == "crates.io":
        have = summarize_crate(fetch_crate(name, locked_version))
        latest = summarize_crate(fetch_crate(name, "latest"))
    else:
        have = summarize(fetch_version(name, locked_version))
        latest = summarize(fetch_version(name, "latest"))
    row = {"name": name, "locked": locked_version, "have": have, "latest": latest, "findings": []}
    if have is None or latest is None:
        row["status"] = "UNCHECKED"
        row["why"] = ("registry did not answer for the locked version" if have is None
                      else "registry did not answer for `latest`")
        return row
    if have.get("yanked"):
        # The release you pinned was withdrawn by its maintainers; installs may still succeed.
        row["findings"].append("LOCKED_YANKED")
    if have.get("rust_version") and latest.get("rust_version") and have["rust_version"] != latest["rust_version"]:
        row["findings"].append("MSRV_CHANGED")
    if have["version"] == latest["version"]:
        row["status"] = "FLAGGED" if row["findings"] else "CURRENT"
        return row
    if have.get("requires_python") and latest.get("requires_python") and have["requires_python"] != latest["requires_python"]:
        row["findings"].append("REQUIRES_PYTHON_CHANGED")
    if have["publisher"] and latest["publisher"] and have["publisher"] != latest["publisher"]:
        row["findings"].append("PUBLISHER_CHANGED")
    if have["license"] != latest["license"]:
        row["findings"].append("LICENSE_CHANGED")
    if have["maintainers"] and latest["maintainers"] and not set(have["maintainers"]) & set(latest["maintainers"]):
        row["findings"].append("MAINTAINERS_REPLACED")
    if latest["deprecated"]:
        row["findings"].append("LATEST_DEPRECATED")
    added_hooks = sorted(set(latest["install_scripts"]) - set(have["install_scripts"]))
    if added_hooks:
        row["findings"].append("INSTALL_SCRIPT_ADDED")
        row["install_scripts_added"] = added_hooks
    if have["provenance"] and not latest["provenance"]:
        row["findings"].append("PROVENANCE_DROPPED")
    a, b = have.get("unpacked_size"), latest.get("unpacked_size")
    if a and b and b >= 3 * a and b - a >= 200_000:
        # A tarball that triples between versions is worth a look; 200 KB floors out tiny packages.
        row["findings"].append("SIZE_JUMP")
        row["size_jump"] = {"from": a, "to": b, "factor": round(b / a, 1)}
    row["status"] = "FLAGGED" if row["findings"] else "BEHIND"
    return row


OSV = "https://api.osv.dev/v1/querybatch"
OSV_FIXTURE = os.environ.get("TRUSTDIFF_OSV_FIXTURE")


def osv_lookup(pairs, ecosystem="npm"):
    """Known vulnerabilities for the exact versions you have, from OSV's public batch API.

    Returns ({name: [ids]}, note). A failed call returns ({}, reason) and every
    package is reported as not checked for vulnerabilities, never as clean.
    """
    if not pairs:
        return {}, None
    if OSV_FIXTURE:
        try:
            fx = json.loads(Path(OSV_FIXTURE).read_text())
            return {n: fx.get(f"{n}@{v}", []) for n, v in pairs if fx.get(f"{n}@{v}")}, None
        except Exception as e:
            return {}, f"fixture unreadable: {e}"
    found = {}
    for start in range(0, len(pairs), 1000):  # OSV caps a batch at 1000 queries
        chunk = pairs[start:start + 1000]
        body = json.dumps({"queries": [{"package": {"name": n, "ecosystem": ecosystem}, "version": v} for n, v in chunk]}).encode()
        req = urllib.request.Request(OSV, data=body, headers={
            "Content-Type": "application/json", "User-Agent": "dependency-trust-diff/0.7 (rote play)"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                results = json.loads(r.read().decode("utf-8", "replace")).get("results", [])
        except Exception as e:
            return {}, f"OSV did not answer ({type(e).__name__}); vulnerabilities were NOT checked"
        for (n, _), res in zip(chunk, results):
            ids = [v.get("id") for v in (res or {}).get("vulns", []) if v.get("id")]
            if ids:
                found[n] = ids
    return found, None


def audit(root, scope="direct", max_packages=200):
    root = Path(root)
    lockfile, pinned, direct, reason = locked_packages(root)
    if pinned is None:
        return {"root": str(root), "ok": False, "lockfile": lockfile, "reason": reason}
    ecosystem = ("PyPI" if lockfile in ("uv.lock", "poetry.lock", "Pipfile.lock") or lockfile.startswith("requirements")
                 else "crates.io" if lockfile == "Cargo.lock" else "npm")
    names = sorted(pinned) if scope == "all" else sorted(n for n in pinned if n in direct)
    skipped = max(0, len(names) - max_packages)
    names = names[:max_packages]
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(lambda n: check(n, pinned[n], ecosystem), names))
    vulns, osv_note = osv_lookup([(n, pinned[n]) for n in names], ecosystem)
    for r in rows:
        ids = vulns.get(r["name"])
        if ids:
            # A known advisory against the exact version you have outranks every
            # other finding here, so it is a finding even on a CURRENT package.
            r["vulns"] = ids[:5]
            r["findings"].insert(0, "KNOWN_VULNERABILITY")
            r["status"] = "FLAGGED"
    by = {}
    for r in rows:
        by.setdefault(r["status"], []).append(r)
    return {
        "root": str(root), "ok": True, "lockfile": lockfile, "ecosystem": ecosystem, "scope": scope,
        "pinned_total": len(pinned), "direct_total": len(direct),
        "checked": len(rows), "skipped_over_max": skipped,
        "source": "fixtures" if FIXTURE_DIR else (PYPI if ecosystem == "PyPI" else CRATES if ecosystem == "crates.io" else REGISTRY),
        "osv": {"checked": osv_note is None, "vulnerable_packages": len(vulns), "note": osv_note},
        "counts": {k: len(v) for k, v in sorted(by.items())},
        "flagged": by.get("FLAGGED", []),
        "unchecked": by.get("UNCHECKED", []),
        "behind": [{"name": r["name"], "locked": r["locked"], "latest": r["latest"]["version"]}
                   for r in by.get("BEHIND", [])],
        "current": [r["name"] for r in by.get("CURRENT", [])],
        "not_checked": ([
            "who uploaded each PyPI release — PyPI's JSON API does not expose the uploader, so publisher changes cannot be seen here; license, yanked status, requires-python and OSV advisories can",
        ] if ecosystem == "PyPI" else []) + [
            "PUBLISHER_CHANGED is the account that ran `npm publish`; a handover to a CI token or a co-maintainer looks identical to a takeover",
            "whether the newer version's code changed behaviour — this reads metadata, never tarballs",
            "transitive packages unless scope=all; version ranges in package.json are ignored, only the lockfile pin counts",
        ],
    }


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: trustdiff.py <repo-dir> [scope=direct|all] [max_packages=N]", file=sys.stderr)
        sys.exit(2)
    root = Path(args[0])
    if not root.is_dir():
        print(f"trustdiff: not a directory: {root}", file=sys.stderr)
        sys.exit(1)
    scope, max_packages = "direct", 200
    for a in args[1:]:
        # accept `scope=all` / `max_packages=50` as well as bare positional `all` / `50`
        k, _, v = a.partition("=")
        v = v if _ else k
        if v in ("direct", "all"):
            scope = v
        elif v.strip().isdigit():
            max_packages = max(1, int(v))
    # compact: rote keeps 65536 bytes of stdout, and indent=2 roughly doubles the size
    json.dump(audit(root, scope, max_packages), sys.stdout, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main()
