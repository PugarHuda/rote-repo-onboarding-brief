#!/usr/bin/env python3
"""Repo onboarding probe: read a repo, emit structured facts as JSON.

Stdlib only (tomllib is 3.11+). No credentials, no network beyond an optional
shallow clone done by the caller. Read-only.
"""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

MANIFESTS = {
    "package.json": "node",
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "pom.xml": "java",
    "build.gradle": "java",
    "Gemfile": "ruby",
    "composer.json": "php",
    "pubspec.yaml": "dart",
}
LOCKFILES = [
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb",
    "uv.lock", "poetry.lock", "Pipfile.lock", "go.sum", "Cargo.lock",
    "composer.lock", "Gemfile.lock",
]
CI_PATHS = [
    ".github/workflows", ".gitlab-ci.yml", ".circleci/config.yml",
    "Jenkinsfile", ".travis.yml", "azure-pipelines.yml",
]
TEST_HINTS = ["test", "tests", "spec", "__tests__", "e2e"]
ENTRY_HINTS = [
    "main.py", "app.py", "__main__.py", "manage.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
    "main.go", "cmd", "src/main.rs", "src/index.ts", "src/index.js",
    "app/page.tsx", "src/App.tsx", "Dockerfile",
]
SECRET_FILES = [".env", ".env.local", ".env.production", "credentials.json", "id_rsa"]
IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
               "build", "target", ".next", ".tox", "vendor", ".mypy_cache"}


def run(args, cwd):
    try:
        p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
        return p.stdout.strip() if p.returncode == 0 else ""
    except Exception:
        return ""


def read_text(path, limit=200_000):
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def find_readme(root):
    for p in sorted(root.iterdir()):
        if p.is_file() and p.name.lower().startswith("readme"):
            return p
    return None


def parse_node(root):
    data = {}
    try:
        pkg = json.loads(read_text(root / "package.json"))
    except Exception:
        return data
    data["name"] = pkg.get("name")
    data["description"] = pkg.get("description")
    data["scripts"] = pkg.get("scripts", {})
    data["engines"] = pkg.get("engines", {})
    data["package_manager"] = pkg.get("packageManager")
    deps = pkg.get("dependencies", {})
    data["dependency_count"] = len(deps) + len(pkg.get("devDependencies", {}))
    data["notable_deps"] = sorted(deps)[:15]
    if pkg.get("workspaces"):
        data["monorepo_workspaces"] = pkg["workspaces"]
    return data


def parse_python(root):
    data = {}
    pp = root / "pyproject.toml"
    if pp.exists():
        try:
            t = tomllib.loads(read_text(pp))
        except Exception:
            return data
        proj = t.get("project", {})
        data["name"] = proj.get("name")
        data["description"] = proj.get("description")
        data["requires_python"] = proj.get("requires-python")
        deps = proj.get("dependencies", []) or []
        data["dependency_count"] = len(deps)
        data["notable_deps"] = deps[:15]
        data["scripts"] = proj.get("scripts", {})
        if "tool" in t:
            data["tooling"] = sorted(t["tool"].keys())
    rt = root / "requirements.txt"
    if rt.exists():
        lines = [l.strip() for l in read_text(rt).splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        data.setdefault("notable_deps", lines[:15])
        data.setdefault("dependency_count", len(lines))
    return data


def parse_go(root):
    data = {}
    txt = read_text(root / "go.mod")
    for line in txt.splitlines():
        if line.startswith("module "):
            data["name"] = line.split(None, 1)[1].strip()
        if line.startswith("go "):
            data["go_version"] = line.split(None, 1)[1].strip()
    data["dependency_count"] = txt.count("\n\t")
    return data


def parse_rust(root):
    data = {}
    try:
        t = tomllib.loads(read_text(root / "Cargo.toml"))
    except Exception:
        return data
    pkg = t.get("package", {})
    data["name"] = pkg.get("name")
    data["description"] = pkg.get("description")
    data["rust_edition"] = pkg.get("edition")
    data["dependency_count"] = len(t.get("dependencies", {}))
    data["notable_deps"] = sorted(t.get("dependencies", {}))[:15]
    return data


def structure(root, max_entries=40):
    out = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except Exception:
        return out
    for p in entries:
        if p.name in IGNORE_DIRS or p.name.startswith("."):
            continue
        if p.is_dir():
            n = 0
            for dirpath, dirnames, filenames in os.walk(p):
                dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
                n += len(filenames)
                if n > 5000:
                    break
            out.append({"path": p.name + "/", "files": n})
        else:
            out.append({"path": p.name, "files": None})
        if len(out) >= max_entries:
            break
    return out


def declared_entry_points(root):
    """Entry points the manifests declare, with the file that declares them. These are
    what the project says it starts from, which beats guessing by filename."""
    out = []
    try:
        pkg = json.loads(read_text(root / "package.json"))
        for k in ("main", "module", "exports"):
            v = pkg.get(k)
            if isinstance(v, str):
                out.append({"kind": f"package.json {k}", "target": v, "exists": (root / v).exists()})
        b = pkg.get("bin")
        if isinstance(b, str):
            out.append({"kind": "package.json bin", "target": b, "exists": (root / b).exists()})
        elif isinstance(b, dict):
            for name, path in list(b.items())[:10]:
                out.append({"kind": f"package.json bin {name}", "target": str(path), "exists": (root / str(path)).exists()})
        for name in ("start", "dev"):
            if name in (pkg.get("scripts") or {}):
                out.append({"kind": f"package.json scripts.{name}", "target": pkg["scripts"][name], "exists": None})
    except Exception:
        pass
    try:
        t = tomllib.loads(read_text(root / "pyproject.toml"))
        for name, target in list(((t.get("project") or {}).get("scripts") or {}).items())[:10]:
            mod = str(target).split(":")[0].replace(".", "/")
            exists = (root / f"{mod}.py").exists() or (root / mod).is_dir() or (root / "src" / f"{mod}.py").exists() or (root / "src" / mod).is_dir()
            out.append({"kind": f"pyproject [project.scripts] {name}", "target": str(target), "exists": exists})
        for name, target in list((((t.get("tool") or {}).get("poetry") or {}).get("scripts") or {}).items())[:10]:
            out.append({"kind": f"pyproject [tool.poetry.scripts] {name}", "target": str(target), "exists": None})
    except Exception:
        pass
    try:
        c = tomllib.loads(read_text(root / "Cargo.toml"))
        for b in (c.get("bin") or [])[:10]:
            if isinstance(b, dict) and b.get("name"):
                path = b.get("path") or f"src/bin/{b['name']}.rs"
                out.append({"kind": f"Cargo [[bin]] {b['name']}", "target": path, "exists": (root / path).exists()})
        if (root / "src" / "main.rs").exists() and (c.get("package") or {}).get("name"):
            out.append({"kind": f"Cargo package {c['package']['name']}", "target": "src/main.rs", "exists": True})
    except Exception:
        pass
    cmd = root / "cmd"
    if cmd.is_dir():
        for d in sorted(p for p in cmd.iterdir() if p.is_dir())[:12]:
            if (d / "main.go").exists():
                out.append({"kind": "go cmd", "target": f"cmd/{d.name}/main.go", "exists": True})
    if (root / "main.go").exists():
        out.append({"kind": "go main package", "target": "main.go", "exists": True})
    return out[:25]


def main():
    # argv may arrive from a step's stdout, so trim stray whitespace/newlines.
    root = Path(sys.argv[1].strip()).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"not a directory: {root}"}))
        return 1

    present = [m for m in MANIFESTS if (root / m).exists()]
    ecosystems = sorted({MANIFESTS[m] for m in present})
    locks = [l for l in LOCKFILES if (root / l).exists()]

    facts = {
        "root": str(root),
        "ecosystems": ecosystems,
        "manifests_present": present,
        "lockfiles_present": locks,
    }

    if "node" in ecosystems:
        facts["node"] = parse_node(root)
    if "python" in ecosystems:
        facts["python"] = parse_python(root)
    if "go" in ecosystems:
        facts["go"] = parse_go(root)
    if "rust" in ecosystems:
        facts["rust"] = parse_rust(root)

    readme = find_readme(root)
    if readme:
        txt = read_text(readme)
        headings = [l.strip() for l in txt.splitlines() if l.strip().startswith("#")][:25]
        facts["readme"] = {
            "file": readme.name,
            "lines": len(txt.splitlines()),
            "headings": headings,
            "first_paragraph": next(
                (l.strip() for l in txt.splitlines()
                 if l.strip() and not l.strip().startswith(("#", "[", "!", "<", "-", "="))),
                None),
        }
    else:
        facts["readme"] = None

    facts["ci"] = [c for c in CI_PATHS if (root / c).exists()]
    if (root / ".github/workflows").is_dir():
        facts["ci_workflows"] = sorted(
            p.name for p in (root / ".github/workflows").iterdir() if p.is_file())

    facts["test_dirs"] = [t for t in TEST_HINTS if (root / t).is_dir()]
    facts["entry_candidates"] = [e for e in ENTRY_HINTS if (root / e).exists()]
    facts["entry_points"] = declared_entry_points(root)
    facts["structure"] = structure(root)
    facts["committed_secret_files"] = [s for s in SECRET_FILES if (root / s).exists()]
    # A local checkout carries files git does not track (.env, build output, a
    # helper script). A stranger's clone will not have them. Name them.
    try:
        st = run(["git", "status", "--porcelain", "--untracked-files=all", "--ignored"], cwd=root)
        untracked = [l[3:] for l in (st or "").splitlines() if l.startswith(("??", "!!"))]
        untracked = [u for u in untracked if not u.startswith((".git/", "node_modules/", ".venv/", "venv/", "__pycache__", "dist/", "build/", "target/", ".next/")) and "/node_modules/" not in u]
        facts["local_only_files"] = {"count": len(untracked), "sample": untracked[:12],
                                     "secret_shaped": [u for u in untracked if u.split("/")[-1] in SECRET_FILES or u.startswith(".env")][:10]}
    except Exception:
        facts["local_only_files"] = None
    facts["has_dockerfile"] = (root / "Dockerfile").exists()
    # Repos spell these many ways: LICENSE, license, LICENSE.md, LICENCE, COPYING.
    # Match on the stem, case-insensitively, or the brief will claim a file is
    # missing while the layout section lists it two lines above.
    top_names = [p.name.lower() for p in root.iterdir() if p.is_file()]
    facts["has_license"] = any(
        n.startswith(("license", "licence", "copying")) for n in top_names)
    facts["has_contributing"] = any(n.startswith("contributing") for n in top_names)

    if (root / ".git").exists():
        shallow = run(["git", "rev-parse", "--is-shallow-repository"], root) == "true"
        git = {
            "shallow_clone": shallow,
            "last_commit_date": run(["git", "log", "-1", "--format=%cI"], root),
            "default_branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root),
        }
        # History depth is meaningless on a shallow clone; report nothing rather
        # than a count that looks real and is not.
        if not shallow:
            git["commit_count"] = run(["git", "rev-list", "--count", "HEAD"], root)
            git["contributors"] = len([l for l in run(
                ["git", "shortlog", "-sn", "--all", "--no-merges"], root).splitlines() if l.strip()])
        facts["git"] = git

    print(json.dumps(facts, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
