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
    "pip", "pip3", "python", "python3", "uv", "uvx", "poetry", "pipenv", "pytest", "tox", "nox", "hatch", "pdm",
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


def _toml(path):
    try:
        import tomllib
        return tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def project_defs(root):
    """Everything else a README command can point at: Cargo bins, Python scripts and
    tox/nox/task names, compose services, Dockerfiles, dev-dependency bins."""
    d = {"cargo_bins": set(), "cargo": False, "py_scripts": set(), "tox_envs": set(),
         "nox_sessions": set(), "task_names": set(), "compose_services": set(),
         "dockerfile": False, "node_dev_bins": set(), "go_mod": False}
    cargo = _toml(root / "Cargo.toml")
    if cargo:
        d["cargo"] = True
        pkg = cargo.get("package") or {}
        if pkg.get("name"):
            d["cargo_bins"].add(pkg["name"])
        for b in cargo.get("bin") or []:
            if isinstance(b, dict) and b.get("name"):
                d["cargo_bins"].add(b["name"])
        for f in sorted((root / "src" / "bin").glob("*.rs")) if (root / "src" / "bin").is_dir() else []:
            d["cargo_bins"].add(f.stem)
    py = _toml(root / "pyproject.toml")
    if py:
        d["py_scripts"].update((py.get("project") or {}).get("scripts", {}) or {})
        poetry = (py.get("tool") or {}).get("poetry") or {}
        d["py_scripts"].update(poetry.get("scripts", {}) or {})
        hatch = ((py.get("tool") or {}).get("hatch") or {}).get("envs") or {}
        for env in hatch.values():
            if isinstance(env, dict):
                d["task_names"].update((env.get("scripts") or {}).keys())
        tox_ini = ((py.get("tool") or {}).get("tox") or {}).get("legacy_tox_ini") or ""
        d["tox_envs"].update(_tox_envlist(tox_ini))
        pdm = ((py.get("tool") or {}).get("pdm") or {}).get("scripts") or {}
        d["task_names"].update(pdm.keys())
    if (root / "tox.ini").is_file():
        d["tox_envs"].update(_tox_envlist(read_text(root / "tox.ini")))
    for nf in ("noxfile.py",):
        if (root / nf).is_file():
            d["nox_sessions"].update(re.findall(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", read_text(root / nf), re.M))
    for tf in ("Taskfile.yml", "Taskfile.yaml", "taskfile.yml"):
        if (root / tf).is_file():
            d["task_names"].update(_yaml_top_keys(read_text(root / tf), "tasks"))
    for cf in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        if (root / cf).is_file():
            d["compose_services"].update(_yaml_top_keys(read_text(root / cf), "services"))
    d["dockerfile"] = (root / "Dockerfile").is_file() or any(root.glob("Dockerfile.*"))
    d["go_mod"] = (root / "go.mod").is_file()
    try:
        pkg = json.loads(read_text(root / "package.json"))
        for f in ("devDependencies", "dependencies"):
            d["node_dev_bins"].update((pkg.get(f) or {}).keys())
    except Exception:
        pass
    return d


def read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _tox_envlist(text):
    m = re.search(r"^\s*env_?list\s*=\s*(.+)$", text, re.M)
    if not m:
        return set()
    return {e.strip() for e in re.split(r"[,\s]+", m.group(1)) if e.strip() and "{" not in e}


def _yaml_top_keys(text, section):
    """Keys directly under a top-level YAML mapping, e.g. the services of a compose file.
    Enough YAML for these two files; avoids a dependency."""
    keys, inside, indent = set(), False, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        cur = len(line) - len(line.lstrip())
        if cur == 0:
            inside = line.rstrip().rstrip(":") == section and line.rstrip().endswith(":")
            indent = None
            continue
        if inside:
            if indent is None:
                indent = cur
            if cur == indent and re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:", line):
                keys.add(re.match(r"^\s*([A-Za-z0-9_.-]+)\s*:", line).group(1))
    return keys


def classify(cmd, scripts, targets, recipes, defs=None):
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

    defs = defs or {}
    root = defs.get("root")

    # `cargo run --bin x` / `cargo build` / `cargo test` -> Cargo.toml and its bins
    if tool == "cargo" and len(parts) >= 2:
        if not defs.get("cargo"):
            return ("undefined", "no Cargo.toml at the repository root", have_tool)
        if "--bin" in parts:
            b = parts[parts.index("--bin") + 1] if parts.index("--bin") + 1 < len(parts) else ""
            if b in defs.get("cargo_bins", set()):
                return ("defined", f"Cargo bin {b}", have_tool)
            return ("undefined", f"no Cargo bin named {b!r}", have_tool)
        return ("defined", "Cargo.toml present", have_tool)

    # `go run ./cmd/x`, `go test ./pkg/...`, `go build .` -> the path must exist
    if tool == "go" and len(parts) >= 2:
        if not defs.get("go_mod"):
            return ("undefined", "no go.mod at the repository root", have_tool)
        paths = [a for a in parts[2:] if a.startswith(".") or a.startswith("/")]
        for a in paths:
            rel = a.replace("/...", "").rstrip("/")
            if rel in (".", "") or (root and (root / rel).exists()):
                continue
            return ("undefined", f"path {a!r} does not exist", have_tool)
        return ("defined", "go.mod present" + (f", path {paths[0]} exists" if paths else ""), have_tool)

    # `npx x` / `pnpm dlx x` -> is x a declared dependency?
    if (tool == "npx" and len(parts) >= 2) or (tool == "pnpm" and len(parts) >= 3 and parts[1] == "dlx"):
        binname = parts[1] if tool == "npx" else parts[2]
        if binname.startswith("-"):
            return ("unknown", "npx flag", have_tool)
        base = binname.split("@")[0] if not binname.startswith("@") else "@" + binname[1:].split("@")[0]
        if base in defs.get("node_dev_bins", set()):
            return ("defined", f"package.json declares {base}", have_tool)
        return ("unknown", f"{base} is not a declared dependency; npx would download it", have_tool)

    # Python task runners
    if tool == "tox" and "-e" in parts:
        env = parts[parts.index("-e") + 1] if parts.index("-e") + 1 < len(parts) else ""
        if env in defs.get("tox_envs", set()):
            return ("defined", f"tox env {env}", have_tool)
        if defs.get("tox_envs"):
            return ("undefined", f"no tox env named {env!r} in envlist", have_tool)
        return ("unknown", "tox envlist not found", have_tool)
    if tool == "nox" and "-s" in parts:
        sess = parts[parts.index("-s") + 1] if parts.index("-s") + 1 < len(parts) else ""
        if sess in defs.get("nox_sessions", set()):
            return ("defined", f"noxfile session {sess}", have_tool)
        return ("undefined", f"no noxfile session named {sess!r}", have_tool)
    if tool in ("poetry", "uv", "hatch", "pdm") and len(parts) >= 3 and parts[1] == "run":
        name = parts[2]
        if name in defs.get("py_scripts", set()) or name in defs.get("task_names", set()):
            return ("defined", f"pyproject script {name}", have_tool)
        if name in ("python", "python3", "pytest", "ruff", "mypy", "black", "flake8", "pip"):
            return ("unknown", f"{tool} run {name}: a tool, not a project script", have_tool)
        if root and (root / name).exists():
            return ("defined", f"file {name} exists", have_tool)
        return ("undefined", f"no pyproject script named {name!r}", have_tool)
    if tool in ("python", "python3") and len(parts) >= 3 and parts[1] == "-m":
        mod = parts[2]
        if mod in ("pip", "venv", "pytest", "http.server", "unittest", "build", "tox", "nox"):
            return ("unknown", f"python -m {mod}: stdlib or tool module", have_tool)
        if root:
            rel = mod.replace(".", "/")
            if (root / f"{rel}.py").exists() or (root / rel).is_dir() or (root / "src" / f"{rel}.py").exists() or (root / "src" / rel).is_dir():
                return ("defined", f"module {mod} exists", have_tool)
            return ("undefined", f"module {mod!r} not found under ./ or ./src", have_tool)
    if tool in ("python", "python3", "pytest") and len(parts) >= 2 and not parts[1].startswith("-"):
        target = parts[1]
        if root and (root / target).exists():
            return ("defined", f"path {target} exists", have_tool)
        if root and "/" in target or target.endswith(".py"):
            return ("undefined", f"path {target!r} does not exist", have_tool)

    # Taskfile
    if tool == "task" and len(parts) >= 2 and not parts[1].startswith("-"):
        if parts[1] in defs.get("task_names", set()):
            return ("defined", f"Taskfile task {parts[1]}", have_tool)
        if defs.get("task_names"):
            return ("undefined", f"no Taskfile task named {parts[1]!r}", have_tool)

    # Docker
    if tool in ("docker", "docker-compose", "podman"):
        sub = parts[1:]
        if sub and sub[0] == "compose":
            sub = sub[1:]
        if tool == "docker-compose" or (parts[1:2] == ["compose"]):
            svcs = [a for a in sub[1:] if not a.startswith("-")]
            missing = [x for x in svcs if x not in defs.get("compose_services", set())]
            if not defs.get("compose_services"):
                return ("undefined", "no compose file at the repository root", have_tool)
            if missing:
                return ("undefined", f"no compose service named {missing[0]!r}", have_tool)
            return ("defined", "compose file present" + (f", service {svcs[0]}" if svcs else ""), have_tool)
        if sub and sub[0] == "build":
            return ("defined" if defs.get("dockerfile") else "undefined",
                    "Dockerfile present" if defs.get("dockerfile") else "no Dockerfile at the repository root", have_tool)

    return ("unknown", "plain command", have_tool)


def main():
    # argv may arrive from a step's stdout, so trim stray whitespace/newlines.
    root = Path(sys.argv[1].strip()).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"not a directory: {root}"}))
        return 1

    scripts, targets, recipes = node_scripts(root), make_targets(root), just_recipes(root)
    defs = project_defs(root)
    defs["root"] = root
    claims = []
    for c in readme_commands(root):
        status, why, have_tool = classify(c["command"], scripts, targets, recipes, defs)
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
        "cargo_bins": sorted(defs["cargo_bins"]),
        "py_scripts": sorted(defs["py_scripts"]),
        "tox_envs": sorted(defs["tox_envs"]),
        "nox_sessions": sorted(defs["nox_sessions"]),
        "task_names": sorted(defs["task_names"]),
        "compose_services": sorted(defs["compose_services"]),
        "claim_count": len(claims),
        "summary": summary,
        "claims": claims,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
