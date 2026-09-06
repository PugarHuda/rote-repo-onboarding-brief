#!/usr/bin/env python3
"""Which package ecosystems in this repository does the update bot actually watch?

Input:  a repository directory, as one argument
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
    ".gitmodules": "gitsubmodule",
    "docker-compose.yml": "docker-compose", "docker-compose.yaml": "docker-compose",
    "compose.yml": "docker-compose", "compose.yaml": "docker-compose",
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


def entry_blocks(text):
    """(start_line, [(indent, stripped)]) for each `- ` item under a top-level `updates:`.

    The file is cut into whole entries first and each entry parsed on its own, so a
    nested `ignore:` or `registries:` list cannot be mistaken for the next entry.
    """
    blocks, in_updates, item_indent, cur = [], False, None, None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.replace("\t", "  ").split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent, stripped = len(line) - len(line.lstrip()), line.strip()
        if indent == 0 and not (in_updates and stripped.startswith("- ")):
            # a new top-level key; cli/cli writes its `- package-ecosystem` items at column 0, so
            # a column-0 list item inside updates: is an entry, not a new key
            in_updates = stripped.startswith("updates:")
            item_indent, cur = None, None
            continue
        if not in_updates:
            continue
        if stripped.startswith("- ") and (item_indent is None or indent <= item_indent):
            item_indent = indent
            cur = (n, [])
            blocks.append(cur)
        if cur is not None:
            cur[1].append((indent, stripped))
    return blocks


IGNORE_KEYS = ("dependency-name", "update-types", "versions", "dependency-type")


def parse_entry(start, rows):
    e = {"line": start, "package-ecosystem": None, "directories": [], "interval": None,
         "limit": None, "registries": [], "ignore": [], "target-branch": None}
    key, ign = None, None
    for indent, s in rows:
        if s.startswith("- "):
            body = s[2:].strip()
            if indent > rows[0][0] or key:
                if key == "ignore":
                    ign = {}
                    e["ignore"].append(ign)
                    if ":" in body:
                        k, v = body.split(":", 1)
                        ign[k.strip()] = v.strip().strip("'\"")
                    continue
                if key in ("directories", "registries"):
                    e[key].append(body.strip("'\""))
                    continue
            s = body
        m = re.match(r"^([a-zA-Z-]+):\s*(.*)$", s)
        if not m:
            continue
        k, v = m.group(1), m.group(2).strip().strip("'\"")
        if ign is not None and k in IGNORE_KEYS:
            ign[k] = v or True
            continue
        ign, key = None, (k if not v else None)
        if k == "package-ecosystem":
            e["package-ecosystem"] = v
        elif k == "directory" and v:
            e["directories"].append(v)
        elif k == "target-branch":
            e["target-branch"] = v
        elif k in ("directories", "registries") and v.startswith("["):
            e[k].extend(x.strip().strip("'\"") for x in v.strip("[]").split(",") if x.strip())
        elif k == "interval":
            e["interval"] = v
        elif k == "open-pull-requests-limit":
            try:
                e["limit"] = int(v)
            except ValueError:
                pass
    return e


def declared_registries(text):
    """Names under the top-level `registries:` map."""
    names, inside = [], False
    for raw in text.splitlines():
        line = raw.replace("\t", "  ").split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if len(line) - len(line.lstrip()) == 0:
            inside = line.strip().startswith("registries:")
            continue
        if inside:
            m = re.match(r"^([A-Za-z0-9_.-]+):", line.strip())
            if m:
                names.append(m.group(1))
    return names


def muted_by_ignore(entry):
    """An ignore rule that swallows every version of every package.

    `dependency-name: "*"` narrowed by update-types or versions is the ordinary way
    to say "no majors" and is never reported; the same wildcard with nothing
    narrowing it means the entry is on the schedule and can never open a version PR.
    """
    for ig in entry["ignore"]:
        if str(ig.get("dependency-name", "")).strip() == "*" and not any(
                k in ig for k in ("update-types", "versions", "dependency-type")):
            return True
    return False


def read_dependabot(text):
    """updates: entries from dependabot.yml. Returns (entries, problems)."""
    entries = [parse_entry(n, rows) for n, rows in entry_blocks(text)]
    declared, problems = declared_registries(text), []
    for e in entries:
        if not e["package-ecosystem"]:
            problems.append({"line": e["line"], "why": "update entry without package-ecosystem"})
        if not e["directories"]:
            problems.append({"line": e["line"], "why": "update entry without directory/directories"})
        for name in e["registries"]:
            if name != "*" and name not in declared:
                problems.append({"line": e["line"], "why": f"names registry {name!r}, which no top-level registries: block declares — Dependabot rejects the file"})
    return entries, problems


IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\s*:")


def strip_jsonc(text):
    """A json5/jsonc config rewritten as the JSON subset json.loads accepts.

    A regex cannot do this: every renovate config carries a URL, and the `//` in
    "https://docs.renovatebot.com/..." is not a comment. Eating it corrupts the
    JSON, the config reads as unparseable, and the play then calls a fully
    covered repository 0% covered — the loudest false negative it could produce.
    So the text is walked once, and only what sits outside a string is touched:
    comments and trailing commas go, single-quoted strings and bare keys — both
    legal json5, both in vuejs/core's own config — become double-quoted.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            quote, i, buf = c, i + 1, []
            while i < n and text[i] != quote:
                if text[i] == chr(92) and i + 1 < n:
                    nxt = text[i + 1]
                    # \' is legal json5 and illegal JSON; every other escape passes through
                    buf.append(nxt if nxt == "'" else text[i:i + 2])
                    i += 2
                    continue
                buf.append('\\"' if text[i] == '"' else text[i])
                i += 1
            i += 1
            out.append('"' + "".join(buf) + '"')
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != chr(10):
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        m = IDENT.match(text, i)
        if m:
            # a bare word followed by ':' can only be a key; true/false/null never are
            out.append('"' + m.group(0).split(":")[0].strip() + '":')
            i = m.end()
            continue
        if c in "}]":
            k = len(out) - 1
            while k >= 0 and out[k] in " \t\r\n":
                k -= 1
            if k >= 0 and out[k] == ",":
                del out[k]
        out.append(c)
        i += 1
    return "".join(out)


def read_renovate(root):
    for name in ("renovate.json", ".renovaterc", ".renovaterc.json", "renovate.json5", ".github/renovate.json", ".github/renovate.json5"):
        p = root / name
        if p.is_file():
            txt = strip_jsonc(p.read_text(encoding="utf-8", errors="replace"))
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
    """Does any `directory`/`directories` value of an entry cover directory d?"""
    for ed in entry_dirs:
        ed = "/" + ed.strip("/") if ed.strip("/") else "/"
        if ed == d:
            return True
        if "**" in ed:
            # /packages/** covers /packages and every directory beneath it
            head = ed.split("**")[0].rstrip("/")
            if d == head or d.startswith(head + "/"):
                return True
            continue
        if fnmatch.fnmatch(d, ed):
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

    covered, uncovered, disabled, muted = [], [], [], []
    for (eco, d), names in sorted(present.items()):
        hits = [e for e in entries if e["package-ecosystem"] == eco and dir_matches(e["directories"], d)]
        row = {"ecosystem": eco, "directory": d, "manifests": sorted(set(names))[:4]}
        if hits:
            if all(h["limit"] == 0 for h in hits):
                row["why"] = "matching entry has open-pull-requests-limit: 0 — security updates only, no version updates"
                disabled.append(row)
            elif all(muted_by_ignore(h) for h in hits):
                row["line"] = hits[0]["line"]
                row["why"] = "matching entry ignores dependency-name: \"*\" with nothing narrowing it — every version update is dropped"
                muted.append(row)
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
        "muted": muted[:40],
        "stale_entries": stale[:40],
        "problems": problems[:20],
        "coverage_pct": round(100.0 * len(covered) / total, 1) if total else None,
        "not_checked": [
            "whether the bot is enabled in the repository's Settings > Code security — the file alone does not turn it on",
            "private registries and `registries:` credentials, which are declared by name only — that a registry is declared is checked, that it authenticates is not",
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
