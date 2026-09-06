#!/usr/bin/env python3
"""Cross-check the commands a repo's README claims against what it actually defines.

Input:  a repository directory, as one argument
Output: JSON list of command claims, each with a status:
          defined         - the repo really defines this (script/target/recipe exists)
          undefined       - the docs claim it, the repo does not define it   <- doc rot
          tool_missing    - defined, but the tool it needs is not on this machine
          unknown         - a plain command we cannot resolve either way

Read-only. Executes nothing from the repo; only probes tool presence with `command -v`.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

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


def shell_blocks(text):
    """Fenced blocks whose language reads as a shell, as (first_body_line, body_lines).

    Walked line by line, not matched by regex: a closing fence must never be read
    as the next opening one, or the prose between two real code blocks is parsed
    as commands. MyST directive fences (```{eval-rst}) fall out of the same walk,
    because their language is not a shell.
    """
    lines, out, i = text.splitlines(), [], 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("```"):
            i += 1
            continue
        lang = lines[i].lstrip()[3:].strip().lower()
        j = i + 1
        while j < len(lines) and not lines[j].lstrip().startswith("```"):
            j += 1
        if lang in SHELL_LANGS:
            out.append((i + 2, lines[i + 1:j]))
        i = j + 1
    return out


def strip_prompt(line):
    line = line.strip()
    for p in ("$ ", "> ", "% ", "# "):
        if line.startswith(p):
            return line[len(p):].strip()
    return line


DOC_NAMES = ("readme", "contributing", "development", "developing", "hacking", "setup", "install", "getting-started", "getting_started")


def doc_files(root, cap=25):
    """README first, then CONTRIBUTING/DEVELOPMENT/… at the root, then docs/*.md. Capped."""
    files = []
    for p in sorted(root.iterdir()):
        if p.is_file() and p.name.lower().startswith("readme"):
            files.append(p)
    for p in sorted(root.iterdir()):
        low = p.name.lower()
        if p.is_file() and any(low.startswith(n) for n in DOC_NAMES[1:]) and low.endswith((".md", ".rst", ".txt")):
            files.append(p)
    for sub in ("docs", "doc"):
        d = root / sub
        if d.is_dir():
            files.extend(sorted(f for f in d.glob("*.md") if f.is_file())[:10])
    return files[:cap]


def readme_commands(root):
    """Every plausible shell command quoted in the repo's docs, with the file and line it came from."""
    out = []
    for p in doc_files(root):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = p.relative_to(root).as_posix()
        for first_line, body in shell_blocks(text):
            for i, raw in enumerate(body):
                line = strip_prompt(raw)
                if not line or line.startswith("#"):
                    continue
                head = line.split()[0] if line.split() else ""
                if head in TOOLS:
                    out.append({"command": line, "source": rel, "line": first_line + i})
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


# ---------------------------------------------------------------------------
# Toolchain floors: what the repo declares vs. what this machine actually has.
# Runs `--version` on YOUR node/python/go/cargo — host tools, never repo code.
# ---------------------------------------------------------------------------
VERSION_FIXTURE = os.environ.get("CLAIMS_TOOL_VERSIONS")  # JSON {tool: "x.y.z"} for offline tests


def declared_floors(root):
    """[(tool, spec, source)] for every runtime floor the repository declares."""
    out = []
    try:
        pkg = json.loads(read_text(root / "package.json"))
        node = (pkg.get("engines") or {}).get("node")
        if node:
            out.append(("node", str(node), "package.json engines.node"))
        pm = pkg.get("packageManager")
        if pm and "@" in pm:
            tool, ver = pm.split("@", 1)
            out.append((tool, "=" + ver.split("+")[0], "package.json packageManager"))
    except Exception:
        pass
    for f in (".nvmrc", ".node-version"):
        if (root / f).is_file():
            v = read_text(root / f).strip().lstrip("v")
            if v and v[0].isdigit():
                out.append(("node", v, f))
    py = _toml(root / "pyproject.toml")
    rp = ((py or {}).get("project") or {}).get("requires-python")
    if rp:
        out.append(("python3", str(rp), "pyproject requires-python"))
    if (root / ".python-version").is_file():
        v = read_text(root / ".python-version").strip().split()[0] if read_text(root / ".python-version").strip() else ""
        if v and v[0].isdigit():
            out.append(("python3", v, ".python-version"))
    gm = read_text(root / "go.mod") if (root / "go.mod").is_file() else ""
    m = re.search(r"^go\s+(\d+(?:\.\d+){0,2})", gm, re.M)
    if m:
        out.append(("go", ">=" + m.group(1), "go.mod go directive"))
    for f in ("rust-toolchain.toml", "rust-toolchain"):
        if (root / f).is_file():
            txt = read_text(root / f)
            m = re.search(r'channel\s*=\s*"([^"]+)"', txt) or re.match(r"^\s*([0-9][^\s]*)\s*$", txt)
            if m and m.group(1)[0].isdigit():
                out.append(("cargo", m.group(1), f))
            break
    # What CI actually tests on, and what the image ships: both are floors a reader
    # will compare against, and both drift away from engines/.nvmrc.
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        for f in sorted(wf.iterdir())[:20]:
            if f.suffix not in (".yml", ".yaml"):
                continue
            txt = read_text(f)
            for key, tool in (("node-version", "node"), ("python-version", "python3"), ("go-version", "go"), ("toolchain", "cargo")):
                for m in re.finditer(rf"^\s*{key}:\s*(.+)$", txt, re.M):
                    raw = m.group(1).strip().strip("'\"")
                    vals = [v.strip().strip("'\"") for v in raw.strip("[]").split(",")] if raw.startswith("[") else [raw]
                    for v in vals:
                        if v and v[0].isdigit() and "${{" not in v:
                            out.append((tool, v + (".x" if v.count(".") == 0 and tool == "node" else ""), f"ci: {f.name}"))
    for df in ["Dockerfile"] + sorted(str(x.name) for x in root.glob("Dockerfile.*")):
        if (root / df).is_file():
            for m in re.finditer(r"^FROM\s+(?:--platform=\S+\s+)?(node|python|golang|rust):([0-9][0-9.]*)", read_text(root / df), re.M):
                tool = {"node": "node", "python": "python3", "golang": "go", "rust": "cargo"}[m.group(1)]
                v = m.group(2).rstrip(".")
                out.append((tool, v + (".x" if v.count(".") < 2 else ""), f"{df} FROM"))
    if (root / ".tool-versions").is_file():
        for line in read_text(root / ".tool-versions").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] in ("nodejs", "python", "golang", "rust"):
                tool = {"nodejs": "node", "python": "python3", "golang": "go", "rust": "cargo"}[parts[0]]
                out.append((tool, parts[1], ".tool-versions"))
    return out


def installed_version(tool):
    """x.y.z of the host tool, or None. Reads only the tool's own --version output."""
    if VERSION_FIXTURE:
        try:
            return json.loads(Path(VERSION_FIXTURE).read_text()).get(tool)
        except Exception:
            return None
    exe = shutil.which(tool)
    if not exe:
        return None
    argv = [exe, "version"] if tool == "go" else [exe, "--version"]
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=8)
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", (p.stdout or "") + (p.stderr or ""))
        return m.group(1) if m else None
    except Exception:
        return None


def _vt(v):
    m = re.match(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", str(v))
    return (int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)) if m else None


def satisfies(spec, version):
    """Does an installed version satisfy a declared range? True/False, or None if unparsed.
    Handles `>=20`, `>=18 <21`, `^18.0.0`, `~3.11`, `20.x`, `1.22`, `a || b`, `stable`."""
    have = _vt(version)
    if have is None:
        return None
    spec = str(spec).strip()
    if spec in ("*", "stable", "latest", "lts/*", "lts"):
        return True
    for clause in spec.split("||"):
        parts = re.findall(r"(>=|<=|>|<|\^|~|=)?\s*v?(\d+(?:\.[0-9x*]+){0,2})", clause)
        if not parts:
            return None
        ok = True
        for op, num in parts:
            want = _vt(num.replace("x", "0").replace("*", "0"))
            segs = num.split(".")
            wild = any(s in ("x", "*") for s in segs)
            # `20.x` compares the one numeric component in front of the wildcard
            depth = next((i for i, s in enumerate(segs) if s in ("x", "*")), len(segs))
            if op == ">=":
                ok &= have >= want
            elif op == ">":
                ok &= have > want
            elif op == "<=":
                ok &= have <= want
            elif op == "<":
                ok &= have < want
            elif op == "~":
                ok &= have[:2] == want[:2] and have >= want
            elif op == "^":
                ok &= (have[0] == want[0] and have >= want) if want[0] else (have[:2] == want[:2] and have >= want)
            else:  # bare or `=`: minimum for go-style `1.22`, exact-prefix for `20.x` / `20.11.0`
                if wild or depth < 3 and not op:
                    ok &= have[:depth] == want[:depth] if wild else have >= want
                else:
                    ok &= have == want
        if ok:
            return True
    return False


def collapse_ci_matrix(floors):
    """A CI matrix is one floor (its lowest version), not ten. Keep the spread in the source label."""
    out, ci = [], {}
    for tool, spec, source in floors:
        if source.startswith("ci: "):
            ci.setdefault((tool, source), []).append(spec)
        else:
            out.append((tool, spec, source))
    for (tool, source), specs in ci.items():
        keyed = sorted(specs, key=lambda v: _vt(v.replace(".x", ".0")) or (0, 0, 0))
        lo, hi = keyed[0], keyed[-1]
        label = source if len(specs) == 1 else f"{source} (tests {len(specs)} versions, {lo} to {hi})"
        out.append((tool, lo, label))
    return out


def toolchain(root):
    rows = []
    for tool, spec, source in collapse_ci_matrix(declared_floors(root)):
        have = installed_version(tool)
        if have is None:
            status = "missing"
        else:
            ok = satisfies(spec, have)
            status = "ok" if ok else ("unparsed" if ok is None else "below_floor")
        rows.append({"tool": tool, "declared": spec, "source": source, "installed": have, "status": status})
    return rows


def floor_conflicts(rows):
    """Two declarations for the same tool that cannot both be met: a pin (.nvmrc 18) that
    the range (engines >=20) rejects. The repository contradicts itself; the reader cannot win."""
    out = []
    by_tool = {}
    for r in rows:
        by_tool.setdefault(r["tool"], []).append(r)
    for tool, decls in by_tool.items():
        for a in decls:
            pin = a["declared"].lstrip("=v")
            if not re.match(r"^\d+(\.(\d+|x))?(\.(\d+|x))?$", pin):
                continue  # only a concrete pin (or `18.x`, a CI matrix major) can be tested against the other ranges
            parts = [p for p in pin.split(".") if p != "x"]
            probe_version = ".".join(parts + ["0"] * (3 - len(parts)))
            for b in decls:
                if b is a or b["declared"] == a["declared"]:
                    continue
                if satisfies(b["declared"], probe_version) is False:
                    out.append({"tool": tool, "a": f"{a['source']} says {a['declared']}", "b": f"{b['source']} says {b['declared']}"})
    return out


def lockfile_conflict(root):
    """More than one Node lockfile committed: which install is real? packageManager decides if set."""
    present = [f for f in ("package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb") if (root / f).is_file()]
    if len(present) < 2:
        return None
    pm = None
    try:
        pm = (json.loads(read_text(root / "package.json")).get("packageManager") or "").split("@")[0] or None
    except Exception:
        pass
    return {"lockfiles": present, "decided_by": f"packageManager = {pm}" if pm else "nothing — no packageManager field; whichever a contributor runs wins"}


LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s#?]+)(?:[#?][^)]*)?\)")


def broken_doc_links(root):
    """Relative links and images in the docs that point at files the repository does not have."""
    broken, checked = [], 0
    for doc in doc_files(root):
        text = read_text(doc)
        for m in LINK_RE.finditer(text):
            target = m.group(1)
            if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith(("//", "mailto")):
                continue  # absolute URL
            checked += 1
            base = doc.parent if not target.startswith("/") else root
            if not (base / target.lstrip("/")).exists():
                line = text.count("\n", 0, m.start()) + 1
                broken.append({"doc": doc.relative_to(root).as_posix(), "line": line, "target": target})
    return {"checked": checked, "broken": broken[:30], "broken_count": len(broken)}



# ---------------------------------------------------------------------------
# Environment variables the code reads, versus the ones the docs admit to.
# The undocumented-and-no-fallback ones are the trap that kills a first run.
# ---------------------------------------------------------------------------
SRC_EXT = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".go", ".rs", ".rb"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "target", ".next", "vendor", "coverage", ".turbo"}
ENV_PATTERNS = [
    # (regex, has_fallback_regex_suffix)
    (re.compile(r"process\.env\.([A-Z][A-Z0-9_]{1,63})"), r"\s*(\|\||\?\?|\?)"),
    (re.compile(r"process\.env\[[\"']([A-Z][A-Z0-9_]{1,63})[\"']\]"), r"\s*(\|\||\?\?|\?)"),
    (re.compile(r"os\.environ\[[\"']([A-Z][A-Z0-9_]{1,63})[\"']\]"), None),
    (re.compile(r"os\.environ\.get\([\"']([A-Z][A-Z0-9_]{1,63})[\"'](,)?"), r","),
    (re.compile(r"os\.getenv\([\"']([A-Z][A-Z0-9_]{1,63})[\"'](,)?"), r","),
    (re.compile(r"os\.Getenv\([\"']([A-Z][A-Z0-9_]{1,63})[\"']\)"), None),
    (re.compile(r"os\.LookupEnv\([\"']([A-Z][A-Z0-9_]{1,63})[\"']\)"), r"."),
    (re.compile(r"env::var(?:_os)?\([\"']([A-Z][A-Z0-9_]{1,63})[\"']\)"), r"\s*\.(unwrap_or|ok\(\)|unwrap_or_else|unwrap_or_default)"),
    (re.compile(r"ENV(?:\.fetch)?[\[(][\"']([A-Z][A-Z0-9_]{1,63})[\"'](,)?"), r","),
]
# Process/shell protocol variables every program may read; never project configuration.
ENV_NOISE = {"NODE_ENV", "PATH", "HOME", "CI", "DEBUG", "TERM", "SHELL", "PWD", "TZ", "LANG", "LC_ALL", "USER", "TMPDIR", "TEMP", "TMP",
             "GITHUB_ACTIONS", "PORT", "NODE_OPTIONS", "npm_config_user_agent", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
             "COMP_WORDS", "COMP_CWORD", "COMP_LINE", "COMP_POINT", "COLUMNS", "LINES", "NO_COLOR", "FORCE_COLOR", "PAGER", "EDITOR", "VISUAL", "LESS",
             "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "SSH_AUTH_SOCK", "DISPLAY", "PYTHONPATH", "VIRTUAL_ENV", "GOPATH", "CARGO_HOME"}


def env_reads(root, max_files=3000):
    """{VAR: {"files": [..], "fallback": bool}} for every env var the source reads."""
    found, n = {}, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in filenames:
            if Path(f).suffix not in SRC_EXT or f.endswith((".d.ts", ".min.js")):
                continue
            n += 1
            if n > max_files:
                return found, True
            path = Path(dirpath) / f
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:400_000]
            except Exception:
                continue
            rel = path.relative_to(root).as_posix()
            if "/test" in f"/{rel}" or rel.startswith("tests/") or ".test." in rel or ".spec." in rel:
                continue
            for rx, fb in ENV_PATTERNS:
                for m in rx.finditer(text):
                    name = m.group(1)
                    if name in ENV_NOISE:
                        continue
                    tail = text[m.end():m.end() + 12]
                    has_fb = bool(fb and re.match(fb, tail)) or (fb == "," and m.lastindex and m.lastindex >= 2 and m.group(2) == ",")
                    e = found.setdefault(name, {"files": [], "fallback": True})
                    if rel not in e["files"] and len(e["files"]) < 5:
                        e["files"].append(rel)
                    e["fallback"] = e["fallback"] and has_fb
    return found, False


def env_documented(root):
    """Names the docs admit to: .env.example-style files, compose env blocks, and the docs text."""
    names, sources = set(), []
    for f in (".env.example", ".env.sample", ".env.template", ".env.dist", "env.example", ".env.defaults"):
        if (root / f).is_file():
            sources.append(f)
            for line in read_text(root / f).splitlines():
                m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,63})\s*=", line)
                if m:
                    names.add(m.group(1))
    for cf in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"):
        if (root / cf).is_file():
            names.update(re.findall(r"^\s*-?\s*([A-Z][A-Z0-9_]{1,63})\s*[:=]", read_text(root / cf), re.M))
    for p in doc_files(root):
        names.update(re.findall(r"\b([A-Z][A-Z0-9_]{2,63})\b", read_text(p)))
    return names, sources


def env_audit(root):
    reads, truncated = env_reads(root)
    documented, sources = env_documented(root)
    undocumented = sorted(n for n in reads if n not in documented)
    return {
        "read_count": len(reads),
        "documented_sources": sources,
        "undocumented_required": [{"name": n, "files": reads[n]["files"]} for n in undocumented if not reads[n]["fallback"]][:40],
        "undocumented_with_fallback": [n for n in undocumented if reads[n]["fallback"]][:40],
        "documented_never_read": sorted(n for n in documented if n not in reads and sources and any(
            re.search(rf"^\s*(?:export\s+)?{re.escape(n)}\s*=", read_text(root / f), re.M) for f in sources))[:20],
        "scan_truncated": truncated,
    }


# ---------------------------------------------------------------------------
# First run: the order a stranger meets things, with the install command the
# lockfile actually implies, so the brief ends in a sequence, not a list.
# ---------------------------------------------------------------------------
INSTALL_BY_LOCK = [
    ("package-lock.json", "npm ci"), ("npm-shrinkwrap.json", "npm ci"),
    ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"), ("yarn.lock", "yarn install --immutable"),
    ("bun.lock", "bun install --frozen-lockfile"), ("bun.lockb", "bun install --frozen-lockfile"),
    ("uv.lock", "uv sync"), ("poetry.lock", "poetry install"), ("Pipfile.lock", "pipenv sync"),
    ("Cargo.lock", "cargo build"), ("go.sum", "go build ./..."), ("Gemfile.lock", "bundle install"),
    ("composer.lock", "composer install"),
]
INSTALL_BY_MANIFEST = [
    ("package.json", "npm install"), ("pyproject.toml", "pip install -e ."), ("requirements.txt", "pip install -r requirements.txt"),
    ("Cargo.toml", "cargo build"), ("go.mod", "go build ./..."), ("Gemfile", "bundle install"), ("composer.json", "composer install"),
]


def first_run(root, scripts, targets, recipes, defs):
    steps = []
    installs = [(f, cmd) for f, cmd in INSTALL_BY_LOCK if (root / f).is_file()]
    if installs:
        for f, cmd in installs[:3]:
            steps.append({"step": "install", "command": cmd, "why": f"{f} is committed, so install from the lock, not from the ranges"})
    else:
        for f, cmd in INSTALL_BY_MANIFEST:
            if (root / f).is_file():
                steps.append({"step": "install", "command": cmd, "why": f"{f} present, no lockfile — versions will float"})
                break
    if "dev" in scripts:
        steps.append({"step": "run", "command": "npm run dev", "why": "package.json scripts.dev"})
    elif "start" in scripts:
        steps.append({"step": "run", "command": "npm start", "why": "package.json scripts.start"})
    for name, cmd, why in (("test", "npm test", "package.json scripts.test"),):
        if name in scripts:
            steps.append({"step": "test", "command": cmd, "why": why})
    if "test" in targets:
        steps.append({"step": "test", "command": "make test", "why": "Makefile target test"})
    if "test" in recipes:
        steps.append({"step": "test", "command": "just test", "why": "justfile recipe test"})
    if defs.get("cargo"):
        steps.append({"step": "test", "command": "cargo test", "why": "Cargo.toml present"})
    if defs.get("go_mod"):
        steps.append({"step": "test", "command": "go test ./...", "why": "go.mod present"})
    if (root / "pyproject.toml").is_file() and any((root / d).is_dir() for d in ("tests", "test")):
        steps.append({"step": "test", "command": "pytest", "why": "pyproject.toml and a tests/ directory"})
    if defs.get("tox_envs"):
        steps.append({"step": "test", "command": "tox", "why": "tox envlist: " + ", ".join(sorted(defs["tox_envs"])[:6])})
    return steps[:8]


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
        if "/" in target:
            # A path into the repository that is not there is stale documentation.
            return ("undefined", f"path {target!r} does not exist", have_tool)
        if target.endswith(".py"):
            # `python hello.py` in a README is usually the file the reader is about
            # to write (click, flask, typer all do this), not a file the repo ships.
            return ("unknown", f"{target} is not in the repository; read as an example the reader creates", have_tool)

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
        "toolchain": toolchain(root),
        "floor_conflicts": floor_conflicts(toolchain(root)),
        "lockfile_conflict": lockfile_conflict(root),
        "doc_links": broken_doc_links(root),
        "env": env_audit(root),
        "first_run": first_run(root, scripts, targets, recipes, defs),
        "docs_scanned": [f.relative_to(root).as_posix() for f in doc_files(root)],
        "claim_count": len(claims),
        "summary": summary,
        "claims": claims,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
