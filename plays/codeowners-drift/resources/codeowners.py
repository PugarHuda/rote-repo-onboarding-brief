#!/usr/bin/env python3
"""Audit a repository's CODEOWNERS file against the files it actually tracks.

Input:  <repo-dir>
Output: JSON — which CODEOWNERS file the forge reads, every rule with how many
        tracked files it matches and how many it actually owns after later
        rules override it, the files no rule covers, and syntax problems.

Read-only. Stdlib only. Executes nothing the repository ships.

Semantics follow GitHub's CODEOWNERS rules: gitignore-style patterns, the LAST
matching rule wins, `docs/*` does not descend into subdirectories, negation
is not supported. GitLab section headers (`[Name]`) are skipped, not parsed.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# GitHub reads exactly one file, in this order of precedence.
LOCATIONS = [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"]

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
               "build", "target", ".next", ".turbo", ".nx", "vendor"}

OWNER_RE = re.compile(r"^(@[\w.-]+(?:/[\w.-]+)?|[^@\s]+@[^@\s]+\.[^@\s]+)$")


def tracked_files(root):
    """Paths git tracks, or a filesystem walk when this is not a git checkout."""
    try:
        p = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                           capture_output=True, timeout=60)
        if p.returncode == 0:
            files = [f for f in p.stdout.decode("utf-8", "replace").split("\0") if f]
            return files, "git ls-files"
    except Exception:
        pass
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in IGNORE_DIRS]
        rel = os.path.relpath(dirpath, root)
        for f in filenames:
            files.append(f if rel == "." else f"{rel}/{f}".replace(os.sep, "/"))
    return sorted(files), "filesystem walk (not a git checkout)"


def compile_pattern(pat):
    """Turn one CODEOWNERS pattern into a compiled regex over repo-relative paths.

    Returns None when the pattern is one CODEOWNERS cannot express.
    """
    if pat.startswith("!"):
        return None
    anchored = pat.startswith("/")
    p = pat.strip("/")
    dir_only = pat.endswith("/")
    if "/" in p:
        anchored = True
    if not p:
        return None
    out, i = "", 0
    while i < len(p):
        c = p[i]
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif p.startswith("**", i):
            out += ".*"
            i += 2
        elif c == "*":
            out += "[^/]*"
            i += 1
        elif c == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(c)
            i += 1
    prefix = "^" if anchored else "^(?:.*/)?"
    last = p.rsplit("/", 1)[-1]
    if dir_only:
        suffix = "/.*$"
    elif "*" in last or "?" in last:
        # GitHub: `docs/*` matches docs/a.md but not docs/b/c.md.
        suffix = "$"
    else:
        # A bare name matches the file or the directory and everything in it.
        suffix = "(?:/.*)?$"
    return re.compile(prefix + out + suffix)


def parse(text):
    """Return (rules, problems). Each rule: line, pattern, owners, regex."""
    rules, problems = [], []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") or line.startswith("^["):
            problems.append({"line": n, "kind": "gitlab_section",
                             "text": raw, "why": "GitLab section header; GitHub ignores it, and this audit skips it"})
            continue
        # Drop a trailing comment, split on unescaped whitespace, unescape `\ `.
        line = re.split(r"\s+#", line, maxsplit=1)[0]
        parts = [t.replace("\\ ", " ") for t in re.split(r"(?<!\\)\s+", line) if t]
        pattern, owners = parts[0], parts[1:]
        regex = compile_pattern(pattern)
        if regex is None:
            why = ("negation is not supported by CODEOWNERS" if pattern.startswith("!")
                   else "pattern is empty after stripping slashes")
            problems.append({"line": n, "kind": "bad_pattern", "text": raw, "why": why})
            continue
        for o in owners:
            if not OWNER_RE.match(o):
                problems.append({"line": n, "kind": "bad_owner", "text": raw,
                                 "why": f"{o!r} is not @user, @org/team, or an email address"})
        rules.append({"line": n, "pattern": pattern, "owners": owners, "regex": regex})
    return rules, problems


def audit(root):
    root = Path(root)
    present = [loc for loc in LOCATIONS if (root / loc).is_file()]
    files, source = tracked_files(root)
    if not present:
        return {
            "root": str(root), "is_present": False,
            "looked_for": LOCATIONS, "file_count": len(files), "file_source": source,
            "reason": "no CODEOWNERS file at any of the three locations GitHub reads: "
                      + ", ".join(LOCATIONS),
        }
    used = present[0]
    rules, problems = parse((root / used).read_text(encoding="utf-8", errors="replace"))

    matched = [0] * len(rules)
    effective = [0] * len(rules)
    owner_files = {}
    unowned = []
    for f in files:
        last = None
        for i, r in enumerate(rules):
            if r["regex"].match(f):
                matched[i] += 1
                last = i
        if last is None:
            unowned.append(f)
            continue
        effective[last] += 1
        if not rules[last]["owners"]:
            # A rule with no owner deliberately clears ownership.
            unowned.append(f)
        for o in rules[last]["owners"]:
            owner_files[o] = owner_files.get(o, 0) + 1

    out_rules = []
    for i, r in enumerate(rules):
        if matched[i] == 0:
            status = "matches nothing"
        elif effective[i] == 0:
            status = "shadowed"
        else:
            status = "ok"
        out_rules.append({"line": r["line"], "pattern": r["pattern"], "owners": r["owners"],
                          "matched": matched[i], "owns": effective[i], "status": status})

    by_dir = {}
    for f in unowned:
        top = f.split("/", 1)[0] if "/" in f else "(root)"
        by_dir[top] = by_dir.get(top, 0) + 1
    unowned_dirs = sorted(by_dir.items(), key=lambda kv: (-kv[1], kv[0]))

    owned = len(files) - len(unowned)
    return {
        "root": str(root), "is_present": True,
        "codeowners_file": used,
        "other_codeowners_files": present[1:],
        "file_count": len(files), "file_source": source,
        "owned_count": owned,
        "coverage_pct": round(100.0 * owned / len(files), 1) if files else 0.0,
        "rules": out_rules,
        "stale_rules": [r for r in out_rules if r["status"] == "matches nothing"],
        "shadowed_rules": [r for r in out_rules if r["status"] == "shadowed"],
        "unowned_count": len(unowned),
        "unowned_by_top_dir": [{"dir": d, "files": c} for d, c in unowned_dirs],
        "unowned_sample": unowned[:25],
        "owners": [{"owner": o, "files": c} for o, c in
                   sorted(owner_files.items(), key=lambda kv: (-kv[1], kv[0]))],
        "problems": problems,
        "not_checked": [
            "whether each @user or @org/team exists and has write access — that needs the forge API",
            "branch protection: whether code owner review is actually required",
        ],
    }


def main():
    if len(sys.argv) != 2:
        print("usage: codeowners.py <repo-dir>", file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"codeowners: not a directory: {root}", file=sys.stderr)
        sys.exit(1)
    json.dump(audit(root), sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
