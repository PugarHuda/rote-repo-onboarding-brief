#!/usr/bin/env python3
"""Which package ecosystems in this repository does the update bot actually watch?

Input:  <repo-dir>
Output: JSON — every manifest directory found, grouped by ecosystem, against the
        `updates:` entries in .github/dependabot.yml (and a renovate config if
        present): covered, uncovered, entries pointing at directories with no
        manifest, and entries disabled with open-pull-requests-limit: 0.

Read-only. Stdlib only. Executes nothing the repository ships. Tiny YAML read
of the one shape dependabot.yml has; a config it cannot read is reported as
unreadable, never as "everything covered".
"""
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target",
        ".next", ".turbo", ".nx", "vendor", "coverage", "site-packages", ".terraform"}

# manifest filename -> dependabot package-ecosystem
MANIFESTS = {
    "package.json": "npm", "requirements.txt": "pip", "requirements-dev.txt": "pip", "pyproject.toml": "pip",
    "Pipfile": "pip", "setup.py": "pip", "go.mod": "gomod", "Cargo.toml": "cargo", "Gemfile": "bundler",
    "composer.json": "composer", "pom.xml": "maven", "build.gradle": "gradle", "build.gradle.kts": "gradle",
    "mix.exs": "mix", "pubspec.yaml": "pub", "Package.swift": "swift", "elm.json": "elm",
    "devcontainer.json": "devcontainers", "Dockerfile": "docker",
}
GLOB_MANIFESTS = {"*.csproj": "nuget", "*.fsproj": "nuget", "*.tf": "terraform", "Dockerfile.*": "docker", "*.dockerfile": "docker"}
MAX_DEPTH = 4


def ecosystems_present(root):
    """{(ecosystem, "/dir"): [manifest names]} for every manifest directory in the tree."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        depth = 0 if rel == "." else rel.count("/") + 1
        dirnames[:] = [d for d in dirnames if d not in SKIP and not (d.startswith(".") and d not in (".github", ".devcontainer"))]
        if depth >= MAX_DEPTH:
            dirnames[:] = []
        dirkey = "/" if rel == "." else "/" + rel
        for f in filenames:
            eco = MANIFESTS.get(f)
            if not eco:
                for pat, e in GLOB_MANIFESTS.items():
                    if fnmatch.fnmatch(f, pat):
                        eco = e
                        break
            if eco:
                found.setdefault((eco, dirkey), []).append(f)
    wf = root / ".github" / "workflows"
    if wf.is_dir() and any(p.suffix in (".yml", ".yaml") for p in wf.iterdir() if p.is_file()):
        found[("github-actions", "/")] = ["workflow files"]
    # A pyproject.toml with only [tool.*] is not a pip manifest; keep it only if it declares deps.
    for (eco, d), names in list(found.items()):
        if eco == "pip" and names == ["pyproject.toml"]:
            try:
                txt = (root / d.lstrip("/") / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
                if "[project]" not in txt and "[tool.poetry]" not in txt:
                    del found[(eco, d)]
            except Exception:
                pass
    return found


def read_dependabot(text):
    """updates: entries from dependabot.yml. Returns (entries, problems)."""
    entries, problems, cur, in_updates, in_dirs = [], [], None, False, False
    item_indent = None  # indentation of the `- ` that starts an update entry; any width is legal YAML
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.replace("\t", "  ").split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and not (in_updates and stripped.startswith("- ")):
            # a new top-level key; cli/cli writes its `- package-ecosystem` items at column 0, so
            # a column-0 list item inside updates: is an entry, not a new key
            in_updates = stripped.startswith("updates:")
            cur, item_indent = None, None
            continue
        if not in_updates:
            continue
        if stripped.startswith("- ") and (item_indent is None or indent <= item_indent):
            item_indent = indent
            cur = {"line": n, "package-ecosystem": None, "directories": [], "interval": None, "limit": None}
            entries.append(cur)
            stripped = stripped[2:].strip()
            in_dirs = False
        if cur is None:
            continue
        m = re.match(r"^([a-zA-Z-]+):\s*(.*)$", stripped)
        if m:
            key, val = m.group(1), m.group(2).strip().strip("'\"")
            in_dirs = False
            if key == "package-ecosystem":
                cur["package-ecosystem"] = val
            elif key == "directory" and val:
                cur["directories"].append(val)
            elif key == "directories":
                in_dirs = True
                if val.startswith("["):
                    cur["directories"].extend(v.strip().strip("'\"") for v in val.strip("[]").split(",") if v.strip())
            elif key == "interval":
                cur["interval"] = val
            elif key == "open-pull-requests-limit":
                try:
                    cur["limit"] = int(val)
                except ValueError:
                    pass
            continue
        if in_dirs and stripped.startswith("- "):
            cur["directories"].append(stripped[2:].strip().strip("'\""))
    for e in entries:
        if not e["package-ecosystem"]:
            problems.append({"line": e["line"], "why": "update entry without package-ecosystem"})
        if not e["directories"]:
            problems.append({"line": e["line"], "why": "update entry without directory/directories"})
    return entries, problems


def read_renovate(root):
    for name in ("renovate.json", ".renovaterc", ".renovaterc.json", "renovate.json5", ".github/renovate.json", ".github/renovate.json5"):
        p = root / name
        if p.is_file():
            txt = p.read_text(encoding="utf-8", errors="replace")
            txt = re.sub(r"//[^\n]*|/\*.*?\*/", "", txt, flags=re.S)  # json5 comments
            txt = re.sub(r",\s*([}\]])", r"\1", txt)
            try:
                cfg = json.loads(txt)
            except Exception:
                return {"file": name, "readable": False}
            return {"file": name, "readable": True, "enabled": cfg.get("enabled", True),
                    "enabled_managers": cfg.get("enabledManagers"), "extends": cfg.get("extends", []),
                    "ignore_paths": cfg.get("ignorePaths", [])}
    return None


RENOVATE_MANAGER = {"npm": "npm", "pip": "pip_requirements", "gomod": "gomod", "cargo": "cargo", "bundler": "bundler",
                    "composer": "composer", "maven": "maven", "gradle": "gradle", "docker": "dockerfile",
                    "github-actions": "github-actions", "terraform": "terraform", "nuget": "nuget", "mix": "mix", "pub": "pub"}


def renovate_ignores(glob, directory, manifests):
    """Does a renovate ignorePaths glob (e.g. **/examples/**, **/test/**) swallow this manifest dir?"""
    rel = directory.strip("/")
    for name in manifests:
        path = f"{rel}/{name}" if rel else name
        g = glob.strip("/")
        # fnmatch has no `**`; translate the two shapes renovate uses
        pat = g.replace("**/", "*/").replace("/**", "/*")
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(path, g) or (g.endswith("/**") and (rel + "/").startswith(g[:-3].rstrip("*") + "/")) or any(seg == g.strip("*/") for seg in rel.split("/")):
            return True
    return False


def dir_matches(entry_dirs, d):
    for ed in entry_dirs:
        ed = "/" + ed.strip("/") if ed.strip("/") else "/"
        if ed == d or fnmatch.fnmatch(d, ed) or fnmatch.fnmatch(d, ed.rstrip("/") + "/*") and ed.endswith("*"):
            return True
    return False


def audit(root):
    root = Path(root)
    present = ecosystems_present(root)
    cfg_path = next((p for p in (".github/dependabot.yml", ".github/dependabot.yaml") if (root / p).is_file()), None)
    entries, problems = ([], [])
    if cfg_path:
        entries, problems = read_dependabot((root / cfg_path).read_text(encoding="utf-8", errors="replace"))
    renovate = read_renovate(root)

    covered, uncovered, disabled = [], [], []
    for (eco, d), names in sorted(present.items()):
        hits = [e for e in entries if e["package-ecosystem"] == eco and dir_matches(e["directories"], d)]
        row = {"ecosystem": eco, "directory": d, "manifests": sorted(set(names))[:4]}
        if hits:
            if all(h["limit"] == 0 for h in hits):
                row["why"] = "matching entry has open-pull-requests-limit: 0 — security updates only, no version updates"
                disabled.append(row)
            else:
                row["interval"] = hits[0]["interval"]
                covered.append(row)
        elif renovate and renovate.get("readable") and renovate.get("enabled", True):
            managers = renovate.get("enabled_managers")
            ignored = [g for g in (renovate.get("ignore_paths") or []) if renovate_ignores(g, d, names)]
            if ignored:
                row["why"] = f"renovate ignorePaths excludes it ({ignored[0]})"
                uncovered.append(row)
            elif managers is None or RENOVATE_MANAGER.get(eco) in managers:
                row["interval"] = "renovate"
                covered.append(row)
            else:
                row["why"] = f"renovate enabledManagers does not include {RENOVATE_MANAGER.get(eco, eco)}"
                uncovered.append(row)
        else:
            row["why"] = "no update entry for this ecosystem and directory"
            uncovered.append(row)

    stale = []
    for e in entries:
        eco = e["package-ecosystem"]
        if not eco:
            continue
        if not any(k[0] == eco and dir_matches(e["directories"], k[1]) for k in present):
            stale.append({"line": e["line"], "ecosystem": eco, "directories": e["directories"][:5],
                          "why": "no manifest of this ecosystem under that directory"})

    total = len(present)
    return {
        "root": str(root),
        "config": cfg_path, "renovate": renovate,
        "entries": len(entries),
        "ecosystems_present": total,
        "covered": covered[:80], "covered_count": len(covered),
        "uncovered": uncovered[:80], "uncovered_count": len(uncovered),
        "disabled": disabled[:40],
        "stale_entries": stale[:40],
        "problems": problems[:20],
        "coverage_pct": round(100.0 * len(covered) / total, 1) if total else None,
        "not_checked": [
            "whether the bot is enabled in the repository's Settings > Code security — the file alone does not turn it on",
            "private registries and `registries:` credentials, which are declared by name only",
            "manifests deeper than 4 directories, and vendored trees, which are skipped",
        ],
    }


def main():
    if len(sys.argv) != 2:
        print("usage: coverage.py <repo-dir>", file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1].strip())
    if not root.is_dir():
        print(f"coverage: not a directory: {root}", file=sys.stderr)
        sys.exit(1)
    json.dump(audit(root), sys.stdout, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main()
