#!/usr/bin/env python3
"""Audit a repository's CODEOWNERS file against the files it actually tracks.

Input:  <repo-dir> [verify_owners=true|false]
Output: JSON — which CODEOWNERS file the forge reads, every rule with how many
        tracked files it matches and how many it actually owns after later
        rules override it, the files no rule covers, syntax problems, and
        (optionally) whether each @owner exists on GitHub.

Read-only. Stdlib only. Executes nothing the repository ships.

GitHub semantics: gitignore-style patterns, the LAST matching rule wins,
`docs/*` does not descend, negation is not supported, one file read in the
order .github/ > root > docs/.
GitLab semantics (detected from `.gitlab/CODEOWNERS` or `[Section]` headers):
sections are evaluated independently, each section's last match wins, a rule
with no owner inherits the section's default owners, `^[Section]` is optional.

verify_owners=true asks api.github.com anonymously whether each @user and
@org exists (60 requests/hour). Team membership needs auth and is never
claimed. Set CODEOWNERS_API_FIXTURES to a JSON file of {path: status} to run
offline.
"""
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

GITHUB_LOCATIONS = [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"]
GITLAB_LOCATIONS = ["CODEOWNERS", ".gitlab/CODEOWNERS", "docs/CODEOWNERS"]

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist",
               "build", "target", ".next", ".turbo", ".nx", "vendor"}

OWNER_RE = re.compile(r"^(@[\w.-]+(?:/[\w.-]+)?|[^@\s]+@[^@\s]+\.[^@\s]+)$")
SECTION_RE = re.compile(r"^(\^)?\[([^\]]+)\](?:\[(\d+)\])?\s*(.*)$")
API = "https://api.github.com"
API_FIXTURES = os.environ.get("CODEOWNERS_API_FIXTURES")
OWNER_CHECK_CAP = 30


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


def parse(text, forge):
    """Return (rules, sections, problems). Each rule: line, pattern, owners, regex, section."""
    rules, sections, problems = [], [], []
    current = None  # section name, or None for the default section
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = SECTION_RE.match(line)
        if m:
            if forge != "gitlab":
                problems.append({"line": n, "kind": "gitlab_section", "text": raw,
                                 "why": "GitLab section header; GitHub ignores it, so every rule under it is read as plain rules"})
                continue
            default_owners = [t for t in re.split(r"(?<!\\)\s+", m.group(4).strip()) if t]
            for o in default_owners:
                if not OWNER_RE.match(o):
                    problems.append({"line": n, "kind": "bad_owner", "text": raw,
                                     "why": f"{o!r} is not @user, @group, or an email address"})
            current = m.group(2).strip()
            sections.append({"name": current, "line": n, "optional": bool(m.group(1)),
                             "approvals": int(m.group(3)) if m.group(3) else None,
                             "default_owners": default_owners, "rules": 0})
            continue
        # Drop a trailing comment, split on unescaped whitespace, unescape `\ `.
        line = re.split(r"\s+#", line, maxsplit=1)[0]
        parts = [t.replace("\\ ", " ") for t in re.split(r"(?<!\\)\s+", line) if t]
        pattern, owners = parts[0], parts[1:]
        regex = compile_pattern(pattern)
        if regex is None:
            why = ("negation is not supported by this audit" + ("" if forge == "gitlab" else " or by GitHub CODEOWNERS")
                   if pattern.startswith("!") else "pattern is empty after stripping slashes")
            problems.append({"line": n, "kind": "bad_pattern", "text": raw, "why": why})
            continue
        for o in owners:
            if not OWNER_RE.match(o):
                problems.append({"line": n, "kind": "bad_owner", "text": raw,
                                 "why": f"{o!r} is not @user, @org/team, or an email address"})
        if forge == "gitlab" and not owners and current is not None:
            owners = list(sections[-1]["default_owners"])  # GitLab: inherit the section default
        if current is not None:
            sections[-1]["rules"] += 1
        rules.append({"line": n, "pattern": pattern, "owners": owners, "regex": regex, "section": current})
    return rules, sections, problems


def api_status(path):
    """HTTP status for GET api.github.com/<path>, or None when the request itself failed."""
    if API_FIXTURES:
        try:
            return json.loads(Path(API_FIXTURES).read_text()).get(path)
        except Exception:
            return None
    req = urllib.request.Request(f"{API}/{path}", headers={
        "User-Agent": "codeowners-drift/0.2 (rote play)", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def verify_owners(owners):
    """Ask GitHub, anonymously, whether each @user / @org exists. Never claims team membership."""
    results, seen_orgs = [], {}
    for o in sorted(owners)[:OWNER_CHECK_CAP]:
        if not o.startswith("@"):
            results.append({"owner": o, "status": "skipped", "why": "email owners are not checkable anonymously"})
            continue
        name = o[1:]
        if "/" in name:
            org = name.split("/", 1)[0]
            code = seen_orgs.get(org)
            if code is None:
                code = seen_orgs[org] = api_status(f"orgs/{org}")
            if code == 200:
                results.append({"owner": o, "status": "org_exists",
                                "why": "organization exists; whether the team exists and has write access needs an authenticated token"})
            elif code == 404:
                results.append({"owner": o, "status": "missing", "why": f"organization @{org} does not exist on github.com"})
            else:
                results.append({"owner": o, "status": "unchecked",
                                "why": "rate limited or no network" if code in (403, 429, None) else f"HTTP {code}"})
        else:
            code = api_status(f"users/{name}")
            if code == 200:
                results.append({"owner": o, "status": "exists", "why": "user or organization exists on github.com"})
            elif code == 404:
                results.append({"owner": o, "status": "missing", "why": "no such user or organization on github.com"})
            else:
                results.append({"owner": o, "status": "unchecked",
                                "why": "rate limited or no network" if code in (403, 429, None) else f"HTTP {code}"})
    return results, max(0, len(owners) - OWNER_CHECK_CAP)


def last_seen(root, pattern):
    """For a stale rule with a literal path, the last commit that still had it.
    Returns {"commit", "date"} / {"never": True} / {"unknown": reason}. Shallow clones
    have no history, and that is said rather than guessed."""
    lit = pattern.strip("/")
    if not lit or any(ch in lit for ch in "*?[") :
        return {"unknown": "wildcard pattern, no single path to look up"}
    try:
        shallow = subprocess.run(["git", "-C", str(root), "rev-parse", "--is-shallow-repository"],
                                 capture_output=True, text=True, timeout=20)
        if shallow.returncode != 0:
            return {"unknown": "not a git checkout"}
        if shallow.stdout.strip() == "true":
            return {"unknown": "shallow clone — history was not fetched"}
        p = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%h %cs", "--", lit],
                           capture_output=True, text=True, timeout=60)
        out = p.stdout.strip()
        if p.returncode != 0:
            return {"unknown": "git log failed"}
        if not out:
            return {"never": True}
        commit, date = out.split(" ", 1)
        return {"commit": commit, "date": date}
    except Exception as e:
        return {"unknown": type(e).__name__}


def audit(root, do_verify=False):
    root = Path(root)
    forge = "gitlab" if (root / ".gitlab/CODEOWNERS").is_file() else "github"
    locations = GITLAB_LOCATIONS if forge == "gitlab" else GITHUB_LOCATIONS
    present = [loc for loc in locations if (root / loc).is_file()]
    files, source = tracked_files(root)
    if not present:
        return {
            "root": str(root), "is_present": False, "forge": forge,
            "looked_for": locations, "file_count": len(files), "file_source": source,
            "reason": f"no CODEOWNERS file at any of the locations {forge.title()} reads: " + ", ".join(locations),
        }
    used = present[0]
    text = (root / used).read_text(encoding="utf-8", errors="replace")
    if forge == "github" and re.search(r"^\^?\[[^\]]+\]", text, re.M) and used == "CODEOWNERS":
        # Section headers in a root CODEOWNERS with no .gitlab/ dir: the file was written for GitLab.
        forge = "gitlab"
    rules, sections, problems = parse(text, forge)

    matched = [0] * len(rules)
    effective = [0] * len(rules)
    owner_files = {}
    unowned, optional_only = [], []
    section_names = [None] + [s["name"] for s in sections] if forge == "gitlab" else [None]
    optional = {s["name"] for s in sections if s["optional"]}
    for f in files:
        winners = {}  # section -> rule index of its last match
        for i, r in enumerate(rules):
            if r["regex"].match(f):
                matched[i] += 1
                winners[r["section"]] = i
        if not winners:
            unowned.append(f)
            continue
        owned_here, required_here = False, False
        for sec, i in winners.items():
            effective[i] += 1
            if rules[i]["owners"]:
                owned_here = True
                if sec not in optional:
                    required_here = True
                for o in rules[i]["owners"]:
                    owner_files[o] = owner_files.get(o, 0) + 1
        if not owned_here:
            unowned.append(f)  # every winning rule clears ownership on purpose
        elif not required_here:
            optional_only.append(f)

    out_rules = []
    for i, r in enumerate(rules):
        status = "matches nothing" if matched[i] == 0 else ("shadowed" if effective[i] == 0 else "ok")
        row = {"line": r["line"], "pattern": r["pattern"], "owners": r["owners"],
               "matched": matched[i], "owns": effective[i], "status": status}
        if r["section"] is not None:
            row["section"] = r["section"]
        out_rules.append(row)

    by_dir = {}
    for f in unowned:
        top = f.split("/", 1)[0] if "/" in f else "(root)"
        by_dir[top] = by_dir.get(top, 0) + 1
    unowned_dirs = sorted(by_dir.items(), key=lambda kv: (-kv[1], kv[0]))

    owned = len(files) - len(unowned)
    # rote captures 65536 bytes of a step's stdout. A 2,000-rule file blows past
    # that as JSON, and a truncated JSON reads as "no audit", so cap the lists and
    # say how much was left out. Problem rows are kept ahead of healthy ones.
    stale = [r for r in out_rules if r["status"] == "matches nothing"]
    for r in stale[:40]:  # one git log each; capped
        r["last_seen"] = last_seen(root, r["pattern"])
    shadow = [r for r in out_rules if r["status"] == "shadowed"]
    RULE_CAP, LIST_CAP = 150, 60

    owner_check, owner_check_omitted, owner_check_note = None, 0, None
    if do_verify:
        if forge == "gitlab":
            owner_check_note = "owner verification is GitHub-only; this file is GitLab-shaped"
        else:
            owner_check, owner_check_omitted = verify_owners(set(owner_files))

    return {
        "root": str(root), "is_present": True, "forge": forge,
        "codeowners_file": used,
        "other_codeowners_files": present[1:],
        "file_count": len(files), "file_source": source,
        "owned_count": owned,
        "coverage_pct": round(100.0 * owned / len(files), 1) if files else 0.0,
        "sections": [{k: v for k, v in s.items()} for s in sections][:LIST_CAP],
        "optional_only_count": len(optional_only),
        "rule_count": len(out_rules),
        "rules": out_rules[:RULE_CAP],
        "rules_omitted": max(0, len(out_rules) - RULE_CAP),
        "stale_count": len(stale),
        "stale_rules": stale[:LIST_CAP],
        "shadowed_count": len(shadow),
        "shadowed_rules": shadow[:LIST_CAP],
        "unowned_count": len(unowned),
        "unowned_by_top_dir": [{"dir": d, "files": c} for d, c in unowned_dirs[:LIST_CAP]],
        "unowned_sample": unowned[:25],
        "owner_count": len(owner_files),
        "owners": [{"owner": o, "files": c} for o, c in
                   sorted(owner_files.items(), key=lambda kv: (-kv[1], kv[0]))[:LIST_CAP]],
        "owner_check": owner_check,
        "owner_check_omitted": owner_check_omitted,
        "owner_check_note": owner_check_note,
        "problems": problems[:LIST_CAP],
        "problem_count": len(problems),
        "not_checked": [
            ("whether each @org/team exists and has write access — GitHub only exposes teams to an authenticated member"
             if do_verify else
             "whether each @user or @org/team exists — pass verify_owners=true to ask api.github.com anonymously"),
            "branch protection: whether code owner review is actually required",
        ],
    }


def main():
    args = sys.argv[1:]
    if not args:
        print("usage: codeowners.py <repo-dir> [verify_owners=true|false]", file=sys.stderr)
        sys.exit(2)
    root = Path(args[0])
    if not root.is_dir():
        print(f"codeowners: not a directory: {root}", file=sys.stderr)
        sys.exit(1)
    do_verify = any(a.split("=")[-1].strip().lower() in ("true", "1", "yes") for a in args[1:])
    # compact: rote keeps 65536 bytes of stdout, and indent=2 roughly doubles the size
    json.dump(audit(root, do_verify), sys.stdout, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main()
