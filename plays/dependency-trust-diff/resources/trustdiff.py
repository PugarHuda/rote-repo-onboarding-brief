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
import os
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REGISTRY = "https://registry.npmjs.org"
FIXTURE_DIR = os.environ.get("TRUSTDIFF_FIXTURE_DIR")
LOCKFILES = ["package-lock.json", "npm-shrinkwrap.json"]
OTHER_LOCKS = ["pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock"]


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
    others = [o for o in OTHER_LOCKS if (root / o).is_file()]
    if others:
        return None, None, None, (f"only {', '.join(others)} found; this Play reads npm's "
                                  "package-lock.json or npm-shrinkwrap.json")
    if (root / "package.json").is_file():
        return None, None, None, "package.json without a lockfile: nothing is pinned, so there is no locked version to compare"
    return None, None, None, "no package.json: not an npm project"


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
        # npm provenance: a Sigstore attestation linking the tarball to the CI run
        # that built it. Present, then absent, means the publish path changed.
        "provenance": bool(dist.get("attestations")),
    }


def check(name, locked_version):
    have = summarize(fetch_version(name, locked_version))
    latest = summarize(fetch_version(name, "latest"))
    row = {"name": name, "locked": locked_version, "have": have, "latest": latest, "findings": []}
    if have is None or latest is None:
        row["status"] = "UNCHECKED"
        row["why"] = ("registry did not answer for the locked version" if have is None
                      else "registry did not answer for `latest`")
        return row
    if have["version"] == latest["version"]:
        row["status"] = "CURRENT"
        return row
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
    row["status"] = "FLAGGED" if row["findings"] else "BEHIND"
    return row


def audit(root, scope="direct", max_packages=200):
    root = Path(root)
    lockfile, pinned, direct, reason = locked_packages(root)
    if pinned is None:
        return {"root": str(root), "ok": False, "lockfile": lockfile, "reason": reason}
    names = sorted(pinned) if scope == "all" else sorted(n for n in pinned if n in direct)
    skipped = max(0, len(names) - max_packages)
    names = names[:max_packages]
    with ThreadPoolExecutor(max_workers=8) as ex:
        rows = list(ex.map(lambda n: check(n, pinned[n]), names))
    by = {}
    for r in rows:
        by.setdefault(r["status"], []).append(r)
    return {
        "root": str(root), "ok": True, "lockfile": lockfile, "scope": scope,
        "pinned_total": len(pinned), "direct_total": len(direct),
        "checked": len(rows), "skipped_over_max": skipped,
        "source": "fixtures" if FIXTURE_DIR else REGISTRY,
        "counts": {k: len(v) for k, v in sorted(by.items())},
        "flagged": by.get("FLAGGED", []),
        "unchecked": by.get("UNCHECKED", []),
        "behind": [{"name": r["name"], "locked": r["locked"], "latest": r["latest"]["version"]}
                   for r in by.get("BEHIND", [])],
        "current": [r["name"] for r in by.get("CURRENT", [])],
        "not_checked": [
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
    json.dump(audit(root, scope, max_packages), sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
