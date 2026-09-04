#!/usr/bin/env python3
"""Cross-check the commands a repo's README claims against what it actually defines.

Input:  <repo-dir>
Output: JSON list of command claims, each with a status:
          defined         - the repo really defines this (script/target/recipe exists)
          undefined       - the docs claim it, the repo does not define it   <- doc rot
          tool_missing    - defined, but the tool it needs is not on this machine
          unknown         - a plain command we cannot resolve either way

Read-only. Executes nothing from the repo; only probes tool presence with `command -v`.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

FENCE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)
SHELL_LANGS = {"", "sh", "bash", "zsh", "shell", "console", "terminal", "commandline", "cmd"}
# Leading tools we recognise as "this is a command someone is meant to run".
TOOLS = {
    "npm", "pnpm", "yarn", "bun", "npx", "node", "deno",
    "pip", "pip3", "python", "python3", "uv", "uvx", "poetry", "pipenv", "pytest", "tox",
    "go", "cargo", "rustc", "make", "just", "task", "mvn", "gradle",
    "docker", "docker-compose", "podman", "bundle", "rake", "composer", "php",
    "dotnet", "swift", "flutter", "dart", "ruby", "gem",
}
SCRIPT_RUNNERS = {"npm", "pnpm", "yarn", "bun"}


def strip_prompt(line):
    line = line.strip()
    for p in ("$ ", "> ", "% ", "# "):
        if line.startswith(p):
            return line[len(p):].strip()
    return line


def readme_commands(root):
    """Every plausible shell command quoted in the repo's README."""
    out = []
    for p in sorted(root.iterdir()):
        if not (p.is_file() and p.name.lower().startswith("readme")):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for lang, body in FENCE.findall(text):
            if lang.lower() not in SHELL_LANGS:
                continue
            for raw in body.splitlines():
                line = strip_prompt(raw)
                if not line or line.startswith("#"):
                    continue
                head = line.split()[0] if line.split() else ""
                if head in TOOLS:
                    out.append({"command": line, "source": p.name})
    # De-duplicate, keep first occurrence order.
    seen, uniq = set(), []
    for c in out:
        if c["command"] not in seen:
            seen.add(c["command"])
            uniq.append(c)
    return uniq


def node_scripts(root):
    try:
        return json.loads((root / "package.json").read_text(encoding="utf-8")).get("scripts", {})
    except Exception:
        return {}


def make_targets(root):
    targets = set()
    for name in ("Makefile", "makefile", "GNUmakefile"):
        f = root / name
        if not f.exists():
            continue
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:(?!=)", line)
                if m:
                    targets.add(m.group(1))
        except Exception:
            pass
    return targets


def just_recipes(root):
    recipes = set()
    f = root / "justfile"
    if not f.exists():
        f = root / "Justfile"
    if f.exists():
        try:
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                # A recipe is `name [params]:` at column 0. Params may carry defaults
                # (`harness harness="codex":`), so anything up to the colon is allowed.
                # `:(?!=)` keeps `x := y` assignments out.
                m = re.match(r"^([a-zA-Z0-9][a-zA-Z0-9_-]*)(?:[ \t][^:\n]*)?:(?!=)", line)
                if m:
                    recipes.add(m.group(1))
        except Exception:
            pass
    return recipes


def classify(cmd, scripts, targets, recipes):
    parts = cmd.split()
    tool = parts[0]
    have_tool = shutil.which(tool) is not None

    # `npm run build`, `pnpm test`, `yarn dev` -> does that script exist?
    if tool in SCRIPT_RUNNERS and len(parts) >= 2:
        sub = parts[1]
        name = parts[2] if sub == "run" and len(parts) >= 3 else sub
        builtin = {"install", "ci", "i", "add", "publish", "init", "create",
                   "exec", "link", "update", "audit", "outdated", "start", "test"}
        if name in scripts:
            return ("defined", f"package.json scripts.{name}", have_tool)
        if sub == "run" or name not in builtin:
            return ("undefined", f"no package.json script named {name!r}", have_tool)
        return ("unknown", f"{tool} builtin", have_tool)

    if tool == "make" and len(parts) >= 2:
        t = parts[1]
        if t in targets:
            return ("defined", f"Makefile target {t}", have_tool)
        return ("undefined", f"no Makefile target named {t!r}", have_tool)

    if tool == "just" and len(parts) >= 2:
        r = parts[1]
        if r in recipes:
            return ("defined", f"justfile recipe {r}", have_tool)
        return ("undefined", f"no justfile recipe named {r!r}", have_tool)

    return ("unknown", "plain command", have_tool)


def main():
    # argv may arrive from a step's stdout, so trim stray whitespace/newlines.
    root = Path(sys.argv[1].strip()).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"not a directory: {root}"}))
        return 1

    scripts, targets, recipes = node_scripts(root), make_targets(root), just_recipes(root)
    claims = []
    for c in readme_commands(root):
        status, why, have_tool = classify(c["command"], scripts, targets, recipes)
        if status == "defined" and not have_tool:
            status, why = "tool_missing", f"{why}; {c['command'].split()[0]!r} not on this machine"
        claims.append({**c, "status": status, "evidence": why, "tool_present": have_tool})

    summary = {}
    for c in claims:
        summary[c["status"]] = summary.get(c["status"], 0) + 1

    print(json.dumps({
        "root": str(root),
        "defined_scripts": sorted(scripts),
        "make_targets": sorted(targets),
        "just_recipes": sorted(recipes),
        "claim_count": len(claims),
        "summary": summary,
        "claims": claims,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
