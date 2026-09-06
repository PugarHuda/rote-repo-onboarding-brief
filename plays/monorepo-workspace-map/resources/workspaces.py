#!/usr/bin/env python3
"""Map a monorepo's workspace packages and the edges between them.

Input:  <repo-dir>
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

    members, skipped = [], []
    for d in dirs:
        info = member_info(d)
        rel = str(d.relative_to(root))
        if not info or not info["name"]:
            skipped.append({"path": rel, "why": "no readable manifest with a name"})
            continue
        members.append({**info, "path": rel})

    names = {m["name"] for m in members}
    edges, external = [], {}
    for m in members:
        for dep, ver in (m["deps"] or {}).items():
            if dep in names:
                edges.append([m["name"], dep])
            else:
                external.setdefault(dep, {})[m["name"]] = ver

    # The same external dependency pinned differently in different packages is
    # the classic monorepo papercut: it builds, then breaks once at runtime.
    skew = []
    for dep, byPkg in external.items():
        distinct = {v for v in byPkg.values() if v}
        if len(distinct) > 1:
            skew.append({"dependency": dep, "versions": byPkg})
    skew.sort(key=lambda s: -len(s["versions"]))

    depended_on = {b for _, b in edges}
    # rote keeps 64KB of a step's stdout; a truncated JSON reads as "no map".
    # Cap the long lists and report how much was left out.
    members_sorted = sorted(members, key=lambda m: m["path"])
    edges_sorted = sorted(edges)
    MEMBER_CAP, EDGE_CAP = 250, 400
    print(json.dumps({
        "is_monorepo": True,
        "root": str(root),
        "workspace_kind": kind,
        "member_count": len(members),
        "members": [{k: v for k, v in m.items() if k != "deps"} for m in members_sorted[:MEMBER_CAP]],
        "members_omitted": max(0, len(members) - MEMBER_CAP),
        "edge_count": len(edges),
        "internal_edges": edges_sorted[:EDGE_CAP],
        "edges_omitted": max(0, len(edges) - EDGE_CAP),
        "leaf_packages": sorted(n for n in names if n not in depended_on),
        "cycles": find_cycles(edges, sorted(names))[:50],
        "version_skew": skew[:25],
        "skipped": skipped[:50],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
