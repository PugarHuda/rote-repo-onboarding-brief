#!/usr/bin/env python3
"""Map a monorepo's workspace packages and the edges between them.

Input:  a repository directory, as one argument
Output: JSON — workspace kind, member packages, internal dependency edges,
        packages nothing depends on, dependency cycles, and version skew
        (the same external dependency pinned differently across packages).

Read-only. Stdlib only. Executes nothing the repository ships.
"""
import json
import re
import sys
import tomllib
from pathlib import Path

IGNORE = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
          "build", "target", ".next", ".turbo", ".nx", "vendor"}


def read(p, limit=400_000):
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def load_json(p):
    try:
        return json.loads(read(p))
    except Exception:
        return None


def load_toml(p):
    try:
        return tomllib.loads(read(p))
    except Exception:
        return None


def expand(root, patterns):
    """Expand workspace globs like 'packages/*' into real directories."""
    dirs = []
    for pat in patterns:
        pat = pat.strip().strip('"').strip("'")
        if not pat or pat.startswith("!"):
            continue
        try:
            for m in sorted(root.glob(pat)):
                if m.is_dir() and not any(part in IGNORE for part in m.parts):
                    dirs.append(m)
        except Exception:
            continue
    # de-dup, keep order
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def detect(root):
    """Return (kind, [member dirs]) for whichever workspace system is in use."""
    pkg = load_json(root / "package.json")
    if pkg and pkg.get("workspaces"):
        ws = pkg["workspaces"]
        pats = ws.get("packages", []) if isinstance(ws, dict) else ws
        return "npm/yarn workspaces", expand(root, pats)

    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.exists():
        # Minimal YAML read: the `packages:` list of quoted globs. Avoids a
        # dependency for the one shape this file ever has.
        pats = re.findall(r"^\s*-\s*['\"]?([^'\"\n]+)['\"]?\s*$", read(pnpm), re.M)
        return "pnpm workspace", expand(root, pats)

    cargo = load_toml(root / "Cargo.toml")
    if cargo and "workspace" in cargo:
        return "cargo workspace", expand(root, cargo["workspace"].get("members", []))

    gowork = root / "go.work"
    if gowork.exists():
        pats = re.findall(r"^\s*\./?([^\s]+)\s*$", read(gowork), re.M)
        return "go workspace", expand(root, [p for p in pats if p not in ("use", "(", ")")])

    pyproject = load_toml(root / "pyproject.toml")
    if pyproject:
        uv = pyproject.get("tool", {}).get("uv", {}).get("workspace", {})
        if uv.get("members"):
            return "uv workspace", expand(root, uv["members"])

    lerna = load_json(root / "lerna.json")
    if lerna and lerna.get("packages"):
        return "lerna", expand(root, lerna["packages"])

    return None, []


def member_info(d):
    """Name, version and dependencies of one workspace member."""
    pkg = load_json(d / "package.json")
    if pkg:
        deps = {}
        for field in ("dependencies", "devDependencies", "peerDependencies"):
            deps.update(pkg.get(field, {}) or {})
        return {"name": pkg.get("name"), "version": pkg.get("version"), "deps": deps}

    cargo = load_toml(d / "Cargo.toml")
    if cargo:
        p = cargo.get("package", {})
        raw = cargo.get("dependencies", {}) or {}
        deps = {k: (v if isinstance(v, str) else (v.get("version") or "workspace"))
                for k, v in raw.items()}
        return {"name": p.get("name"), "version": p.get("version"), "deps": deps}

    py = load_toml(d / "pyproject.toml")
    if py:
        proj = py.get("project", {})
        deps = {}
        for spec in proj.get("dependencies", []) or []:
            m = re.match(r"^\s*([A-Za-z0-9._-]+)\s*(.*)$", spec)
            if m:
                deps[m.group(1)] = m.group(2).strip() or "*"
        return {"name": proj.get("name"), "version": proj.get("version"), "deps": deps}
    return None


def _semver(v):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def range_satisfied(spec, version):
    """Does `version` satisfy a simple npm/Cargo range? Returns True, False, or None
    when the range is a workspace link, a catalog, a URL, or a shape not modelled here."""
    spec = str(spec or "").strip()
    if not spec or spec in ("*", "latest") or spec.startswith(("workspace:", "catalog:", "file:", "link:", "npm:", "git", "http")):
        return None
    have = _semver(version)
    if have is None:
        return None
    if "||" in spec or " - " in spec or spec.startswith(("<", ">")) or " " in spec:
        return None
    op, rest = "", spec
    if spec[0] in "^~=":
        op, rest = spec[0], spec[1:]
    want = _semver(rest)
    if want is None:
        # Cargo-style "1.2" or npm "1" -> caret on the given components
        m = re.match(r"^(\d+)(?:\.(\d+))?$", rest)
        if not m:
            return None
        want = (int(m.group(1)), int(m.group(2) or 0), 0)
        op = op or "^"
    if op == "=" or op == "":
        return have == want if re.match(r"^\d+\.\d+\.\d+$", rest) else have[:2] == want[:2] if have[0] == want[0] else False
    if op == "~":
        return have[:2] == want[:2] and have >= want
    if op == "^":
        if want[0] > 0:
            return have[0] == want[0] and have >= want
        if want[1] > 0:
            return have[:2] == want[:2] and have >= want
        return have == want
    return None


def unlisted_packages(root, member_dirs, max_depth=3):
    """Directories holding a package.json (or Cargo.toml) that no workspace glob covers."""
    listed = [d.resolve() for d in member_dirs]
    found = []
    for manifest in ("package.json", "Cargo.toml"):
        for f in root.rglob(manifest):
            d = f.parent
            rel = d.relative_to(root)
            if d == root or any(part in IGNORE for part in rel.parts) or len(rel.parts) > max_depth:
                continue
            rd = d.resolve()
            # A manifest inside a member is that member's sub-entry (vue ships
            # packages/vue/compiler-sfc/package.json), not a forgotten package.
            if any(rd == m or m in rd.parents for m in listed):
                continue
            found.append(rel.as_posix())
    return sorted(set(found))


def pipeline_coverage(root, members, dirs):
    """turbo.json (v2 `tasks`, v1 `pipeline`) or nx.json (`targetDefaults`) names tasks every
    package is expected to run. A package without that script is skipped silently; list them."""
    turbo = load_json(root / "turbo.json")
    nx = load_json(root / "nx.json") if not isinstance(turbo, dict) else None
    if isinstance(turbo, dict):
        tasks = turbo.get("tasks") if isinstance(turbo.get("tasks"), dict) else turbo.get("pipeline")
        pipeline_file = "turbo.json"
    elif isinstance(nx, dict):
        tasks = nx.get("targetDefaults")
        pipeline_file = "nx.json"
    else:
        return None
    if not isinstance(tasks, dict):
        return None
    # plain task names only: nx also keys executors (@nx/jest:jest) and glob patterns (e2e-ci--**/*)
    names = sorted({t.split("#", 1)[-1] for t in tasks
                    if not t.startswith("//") and re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", t.split("#", 1)[-1])})[:12]
    scripts_by = {}
    for d, m in zip(dirs, members):
        pkg = load_json(d / "package.json") or {}
        scripts_by[m["name"]] = set((pkg.get("scripts") or {}).keys())
    out = []
    for t in names:
        missing = sorted(n for n, sc in scripts_by.items() if t not in sc)
        out.append({"task": t, "packages_with_script": len(scripts_by) - len(missing),
                    "packages_without": missing[:25], "without_count": len(missing)})
    return {"file": pipeline_file, "tasks": out}


def find_cycles(edges, names):
    """Every dependency cycle among workspace members."""
    graph = {n: [] for n in names}
    for a, b in edges:
        graph.setdefault(a, []).append(b)
    cycles, state, stack = [], {}, []

    def walk(n):
        state[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if state.get(m, 0) == 0:
                walk(m)
            elif state.get(m) == 1:
                cycles.append(stack[stack.index(m):] + [m])
        stack.pop()
        state[n] = 2

    for n in names:
        if state.get(n, 0) == 0:
            walk(n)
    return cycles


def main():
    root = Path(sys.argv[1].strip()).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"not a directory: {root}"}))
        return 1

    kind, dirs = detect(root)
    if not kind:
        print(json.dumps({
            "is_monorepo": False,
            "root": str(root),
            "reason": "no workspace definition found (checked package.json workspaces, "
                      "pnpm-workspace.yaml, Cargo.toml [workspace], go.work, "
                      "pyproject [tool.uv.workspace], lerna.json)",
        }, indent=2))
        return 0

    members, skipped, member_dirs = [], [], []
    for d in dirs:
        info = member_info(d)
        rel = str(d.relative_to(root))
        if not info or not info["name"]:
            skipped.append({"path": rel, "why": "no readable manifest with a name"})
            continue
        members.append({**info, "path": rel})
        member_dirs.append(d)
    pipeline = pipeline_coverage(root, members, member_dirs)

    names = {m["name"] for m in members}
    versions = {m["name"]: m.get("version") for m in members}
    edges, external, mismatches = [], {}, []
    for m in members:
        for dep, ver in (m["deps"] or {}).items():
            if dep in names:
                edges.append([m["name"], dep])
                ok = range_satisfied(ver, versions.get(dep))
                if ok is False:
                    # The workspace copy does not satisfy the range, so the package
                    # manager resolves it from the registry instead of linking it.
                    mismatches.append({"package": m["name"], "depends_on": dep,
                                       "wants": str(ver), "workspace_has": str(versions.get(dep))})
            else:
                external.setdefault(dep, {})[m["name"]] = ver

    # The same external dependency pinned differently in different packages is
    # the classic monorepo papercut: it builds, then breaks once at runtime.
    skew = []
    for dep, byPkg in external.items():
        distinct = {v for v in byPkg.values() if v}
        if len(distinct) > 1:
            # One dependency used by 140 packages is 140 lines of JSON; keep the
            # distinct pins and a sample of who holds each, under rote's 64KB cap.
            sample = dict(sorted(byPkg.items())[:12])
            skew.append({"dependency": dep, "versions": sample, "package_count": len(byPkg),
                         "distinct_versions": sorted(distinct)[:10]})
    skew.sort(key=lambda s: -s["package_count"])

    depended_on = {b for _, b in edges}
    # rote keeps 64KB of a step's stdout; a truncated JSON reads as "no map".
    # Cap the long lists and report how much was left out.
    members_sorted = sorted(members, key=lambda m: m["path"])
    edges_sorted = sorted(edges)
    leaves = sorted(n for n in names if n not in depended_on)
    # A 300-package ring produces cycles 300 names long; keep each cycle readable.
    cycles = [c if len(c) <= 40 else c[:40] + [f"… +{len(c) - 40} more"]
              for c in find_cycles(edges, sorted(names))[:20]]
    unlisted = unlisted_packages(root, dirs)[:50]
    member_cap, edge_cap = 250, 400
    STDOUT_BUDGET = 60_000  # rote keeps 65536 bytes; leave headroom for the runner
    while True:
        payload = {
            "is_monorepo": True,
            "root": str(root),
            "workspace_kind": kind,
            "member_count": len(members),
            "members": [{k: v for k, v in m.items() if k != "deps"} for m in members_sorted[:member_cap]],
            "members_omitted": max(0, len(members) - member_cap),
            "edge_count": len(edges),
            "internal_edges": edges_sorted[:edge_cap],
            "edges_omitted": max(0, len(edges) - edge_cap),
            "leaf_packages": leaves[:200],
            "cycles": cycles,
            "version_skew": skew[:25],
            "internal_version_mismatch": mismatches[:50],
            "unlisted_packages": unlisted,
            "pipeline": pipeline,
            "skipped": skipped[:50],
        }
        text = json.dumps(payload, separators=(",", ":"))
        # babel: 162 members and 761 edges came to 76 KB pretty-printed. Halve the
        # long lists until the whole thing fits; the omitted counts stay honest.
        if len(text.encode()) <= STDOUT_BUDGET or (member_cap <= 10 and edge_cap <= 10 and len(cycles) <= 2):
            break
        member_cap, edge_cap = max(10, member_cap // 2), max(10, edge_cap // 2)
        cycles = cycles[:max(2, len(cycles) // 2)]
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
