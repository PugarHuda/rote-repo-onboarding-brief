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
    facts["structure"] = structure(root)
    facts["committed_secret_files"] = [s for s in SECRET_FILES if (root / s).exists()]
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
