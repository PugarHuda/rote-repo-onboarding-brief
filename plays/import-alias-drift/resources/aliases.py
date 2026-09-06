#!/usr/bin/env python3
"""Do TypeScript and the bundler agree about what `@/thing` means?

Input:  a repository directory, as one argument
Output: JSON — every path alias declared in a tsconfig/jsconfig, whether its
        target exists, how many files import it, and whether each resolver that
        has to mirror it (vite, vitest, jest, webpack, rollup) actually does.

Read-only. Stdlib only. Nothing the repository ships is ever executed — the
bundler configs are read as text, which is exactly what this cannot fully know,
and it says so.
"""
import json
import os
import re
import sys
from pathlib import Path

SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", "out",
        ".next", ".nuxt", ".turbo", ".nx", "vendor", "coverage", ".output", ".svelte-kit"}
SRC_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".astro"}
MAX_FILES = 6000
MAX_CONFIGS = 40
IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\s*:")


def strip_jsonc(text):
    """A jsonc/json5 config rewritten as the JSON subset json.loads accepts.

    tsconfig.json is JSON with comments by convention and trailing commas in
    practice. A regex cannot strip those: these files are full of paths and
    URLs whose `//` is not a comment. So the text is walked once, and only what
    sits outside a string is touched.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' or c == "'":
            quote, i, buf = c, i + 1, []
            while i < n and text[i] != quote:
                if text[i] == chr(92) and i + 1 < n:
                    nxt = text[i + 1]
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


def read_json(path):
    try:
        return json.loads(strip_jsonc(path.read_text(encoding="utf-8", errors="replace")))
    except Exception:
        return None


def walk(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP and not d.startswith(".")]
        yield Path(dirpath), dirnames, filenames


def find_configs(root):
    """Every tsconfig/jsconfig in the tree, shallowest first, capped."""
    found = []
    for dirpath, _dirnames, filenames in walk(root):
        for f in filenames:
            if f == "jsconfig.json" or (f.startswith("tsconfig") and f.endswith(".json")):
                found.append(dirpath / f)
    found.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))
    return found[:MAX_CONFIGS]


def resolve_extends(spec, cfg_path, root):
    """`extends` is a relative path or a package name. Returns (path or None, why)."""
    if spec.startswith("."):
        p = (cfg_path.parent / spec).resolve()
        for cand in (p, Path(str(p) + ".json"), p / "tsconfig.json"):
            if cand.is_file():
                return cand, None
        return None, "extends " + repr(spec) + " — no such file"
    for base in (cfg_path.parent, root):
        p = base / "node_modules" / spec
        for cand in (p, Path(str(p) + ".json"), p / "tsconfig.json"):
            if cand.is_file():
                return cand, None
    return None, ("extends " + repr(spec) + " — a package, and node_modules is not in this "
                  "checkout, so any paths it declares were not read")


def load_config(cfg_path, root, depth=0):
    """compilerOptions.paths and baseUrl, with the extends chain merged in."""
    data = read_json(cfg_path)
    if data is None:
        try:
            named = cfg_path.relative_to(root).as_posix()
        except ValueError:
            named = cfg_path.name
        return None, [named + " could not be parsed, so its aliases were not read"]
    notes, paths, base_url = [], {}, None
    ext = data.get("extends")
    specs = [ext] if isinstance(ext, str) else ext if isinstance(ext, list) else []
    for spec in specs:
        if depth >= 5 or not isinstance(spec, str):
            break
        parent_path, why = resolve_extends(spec, cfg_path, root)
        if why:
            notes.append(why)
            continue
        parent, pnotes = load_config(parent_path, root, depth + 1)
        notes.extend(pnotes)
        if parent:
            paths.update(parent["paths"])
            base_url = base_url or parent["base_url"]
    co = data.get("compilerOptions") or {}
    if isinstance(co.get("paths"), dict):
        for k, v in co["paths"].items():
            paths[k] = [str(x) for x in v] if isinstance(v, list) else [str(v)]
    if isinstance(co.get("baseUrl"), str):
        base_url = co["baseUrl"]
    refs = [r.get("path") for r in (data.get("references") or []) if isinstance(r, dict)]
    return {"file": cfg_path, "paths": paths, "base_url": base_url, "references": refs}, notes


BUILT_DIRS = ("lib", "dist", "build", "out", "es", "esm", "cjs", "types", "typings")
GENERATED = set(BUILT_DIRS) | {"node_modules", ".nuxt", ".next", ".output", ".svelte-kit", ".astro", ".vercel"}


def target_state(cfg, target):
    """Is a paths target on disk? "missing" and "not built yet" are different answers.

    TS resolves the target against baseUrl when set, else the config's own
    directory. A target inside node_modules or a build output directory is
    absent from a fresh checkout by design, so calling it dead would be wrong —
    it is reported as not checked instead.
    """
    base = cfg["file"].parent / (cfg["base_url"] or ".")
    literal = target.split("*")[0]
    p = (base / literal).resolve()
    if p.is_dir() or p.exists():
        return "present"
    if "*" in target and p.parent.is_dir():
        return "present"
    for ext in (".ts", ".tsx", ".d.ts", ".js", ".jsx", ".json", "/index.ts", "/index.js"):
        if Path(str(p) + ext).exists():
            return "present"
    parts = target.replace("\\", "/").split("/")
    if "node_modules" in parts:
        return "not_installed"
    if any(seg in BUILT_DIRS for seg in parts):
        return "not_built"
    return "missing"


ALIAS_KEY = re.compile(r"""['"]([^'"\s]{1,60})['"]\s*:""")
FIND_KEY = re.compile(r"""find\s*:\s*['"]([^'"]{1,60})['"]""")
BLOCK = re.compile(r"(?:alias|moduleNameMapper)\s*:\s*([{\[])")

RESOLVERS = [
    ("vite", ("vite.config.ts", "vite.config.js", "vite.config.mts", "vite.config.mjs")),
    ("vitest", ("vitest.config.ts", "vitest.config.js", "vitest.config.mts")),
    ("jest", ("jest.config.js", "jest.config.ts", "jest.config.cjs", "jest.config.mjs", "jest.config.json")),
    ("webpack", ("webpack.config.js", "webpack.config.ts", "webpack.common.js")),
    ("rollup", ("rollup.config.js", "rollup.config.ts", "rollup.config.mjs")),
    ("next", ("next.config.js", "next.config.ts", "next.config.mjs")),
]
# Each of these makes the resolver read tsconfig itself, so it cannot drift.
MIRRORS = {
    "vite-tsconfig-paths": "vite-tsconfig-paths reads tsconfig, so vite follows it",
    "pathsToModuleNameMapper": "ts-jest's pathsToModuleNameMapper builds the mapper from tsconfig",
    "tsconfig-paths-webpack-plugin": "tsconfig-paths-webpack-plugin reads tsconfig, so webpack follows it",
    "TsconfigPathsPlugin": "tsconfig-paths-webpack-plugin reads tsconfig, so webpack follows it",
    "@rollup/plugin-typescript": "the rollup typescript plugin resolves through tsconfig",
}
NOT_A_KEY = {"find", "replacement", "customResolver"}


def extract_keys(text):
    """Alias keys named in a bundler config, read as text.

    The config is a program, not data. Only the literal keys of an `alias` or
    `moduleNameMapper` block are read, and a block whose keys are computed or
    spread in is reported as unreadable rather than as empty — "no aliases
    here" and "I could not tell" are different statements.
    """
    keys, computed = set(), False
    for m in BLOCK.finditer(text):
        open_i = m.end() - 1
        depth, j, n = 0, open_i, len(text)
        while j < n:
            if text[j] in "{[":
                depth += 1
            elif text[j] in "}]":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = text[open_i:j + 1]
        found = set(ALIAS_KEY.findall(body)) | set(FIND_KEY.findall(body))
        found -= NOT_A_KEY
        if not found and re.search(r"\.\.\.|\w+\(", body):
            computed = True
        keys |= found
    return keys, computed


def read_resolvers(root):
    out = []
    pkg = read_json(root / "package.json") or {}
    deps = dict(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})
    for name, filenames in RESOLVERS:
        path = next((root / f for f in filenames if (root / f).is_file()), None)
        if name == "jest" and path is None and isinstance(pkg.get("jest"), dict):
            mapper = pkg["jest"].get("moduleNameMapper") or {}
            out.append({"resolver": "jest", "file": "package.json (jest field)",
                        "keys": sorted(mapper.keys()), "mirrored": None, "unreadable": False})
            continue
        if path is None:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        mirror = next((why for token, why in MIRRORS.items() if token in text or token in deps), None)
        if name == "next":
            mirror = mirror or "Next.js reads compilerOptions.paths from tsconfig itself"
        keys, computed = (set(), False) if mirror else extract_keys(text)
        out.append({"resolver": name, "file": path.name, "keys": sorted(keys),
                    "mirrored": mirror, "unreadable": bool(computed and not keys)})
    return out


IMPORT = re.compile(r"""(?:from|import|require)\s*\(?\s*['"]([^'"\n]{1,120})['"]""")


def count_imports(root, prefixes):
    """How many files import each alias, and how many of those are tests."""
    counts = {p: {"files": 0, "test_files": 0} for p in prefixes}
    scanned = 0
    for dirpath, _dirnames, filenames in walk(root):
        for f in filenames:
            if Path(f).suffix not in SRC_EXT:
                continue
            if scanned >= MAX_FILES:
                return counts, scanned, True
            p = dirpath / f
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            scanned += 1
            rel = p.relative_to(root).as_posix().lower()
            is_test = any(t in rel for t in ("test", "spec", "__mocks__", "e2e", "cypress"))
            hit = {pref for spec in IMPORT.findall(text) for pref in prefixes
                   if spec == pref.rstrip("/") or spec.startswith(pref)}
            for pref in hit:
                counts[pref]["files"] += 1
                counts[pref]["test_files"] += 1 if is_test else 0
    return counts, scanned, False


def prefix_of(alias):
    """`@/*` and `@ui/*` are matched by the literal part before the star."""
    return alias.split("*")[0]


def bare_key(key):
    """A jest moduleNameMapper key is a regex; reduce it to the module prefix it stands for."""
    return key.strip("^$").replace("(.*)", "").replace(".*", "").replace("\\", "")


def is_alias_key(key):
    """`^@/(.*)$` is an alias. `\\.(css|sass)$` is an asset stub, not a module path."""
    bare = bare_key(key)
    return bool(bare) and not bare.startswith(".") and "|" not in bare


def covers(key, alias, prefix):
    """Does a resolver key stand for the same alias?

    Both directions matter. A wildcard alias `@/*` covers a narrower resolver key
    `^@/test$`; a broad resolver key `^@/(.*)$` covers a narrower alias. Only one
    of those is obvious, and missing the other reports a mapped alias as unmapped.
    """
    bare = bare_key(key)
    if not bare:
        return False
    if key == alias or bare.rstrip("/") == prefix.rstrip("/"):
        return True
    if prefix.startswith(bare):
        return True
    return "*" in alias and bare.startswith(prefix)


def audit(root):
    root = Path(root)
    configs, notes, bad_refs = [], [], []
    for cfg_path in find_configs(root):
        cfg, cnotes = load_config(cfg_path, root)
        notes.extend(cnotes)
        if not cfg:
            continue
        for ref in cfg["references"]:
            if not isinstance(ref, str):
                continue
            # .nuxt, .next, dist and friends are generated: absent from a fresh
            # checkout by design, so a reference into one is not a broken reference
            if any(seg in GENERATED for seg in ref.replace("\\", "/").split("/")):
                continue
            p = (cfg_path.parent / ref).resolve()
            if not (p.is_file() or (p / "tsconfig.json").is_file()):
                bad_refs.append({"from": cfg_path.relative_to(root).as_posix(), "path": ref})
        if cfg["paths"]:
            configs.append(cfg)

    aliases, seen = [], set()
    for cfg in configs:
        rel = cfg["file"].relative_to(root).as_posix()
        at_root = cfg["file"].parent == root
        for name, targets in sorted(cfg["paths"].items()):
            if name in seen:
                continue
            seen.add(name)
            states = [target_state(cfg, t) for t in targets]
            worst = "present" if "present" in states else states[0]
            aliases.append({"alias": name, "targets": targets[:4], "config": rel, "at_root": at_root,
                            "prefix": prefix_of(name), "target_state": worst})

    resolvers = read_resolvers(root)
    prefixes = sorted({a["prefix"] for a in aliases if a["prefix"]})
    counts, scanned, capped = count_imports(root, prefixes) if prefixes else ({}, 0, False)

    drift, dead, unbuilt, unused, resolver_only = [], [], [], [], []
    for a in aliases:
        use = counts.get(a["prefix"], {"files": 0, "test_files": 0})
        a["files"], a["test_files"] = use["files"], use["test_files"]
        if a["target_state"] == "missing":
            dead.append(a)
            continue
        if a["target_state"] in ("not_built", "not_installed"):
            unbuilt.append(a)
            continue
        # The resolver configs read here are the ones at the repository root, so
        # only aliases from a root tsconfig are theirs to mirror. A tsconfig deep
        # in a test corpus answers to a build this play never saw, and an alias
        # nothing at the root declares is not evidence that anything is unused.
        if not a["at_root"]:
            continue
        if not use["files"] and not capped:
            unused.append(a["alias"])
        for r in resolvers:
            if r["mirrored"] or r["unreadable"] or not r["keys"]:
                continue
            if not any(covers(k, a["alias"], a["prefix"]) for k in r["keys"]):
                drift.append({"alias": a["alias"], "config": a["config"], "resolver": r["resolver"],
                              "resolver_file": r["file"], "files": use["files"],
                              "test_files": use["test_files"]})

    for r in resolvers:
        if r["mirrored"] or r["unreadable"]:
            continue
        for k in r["keys"]:
            # compared against every tsconfig in the tree: a monorepo often keeps
            # the resolver at the root and the paths one directory down
            if is_alias_key(k) and not any(covers(k, a["alias"], a["prefix"]) for a in aliases):
                resolver_only.append({"alias": k, "resolver": r["resolver"], "resolver_file": r["file"]})

    return {
        "root": str(root),
        "configs": [c["file"].relative_to(root).as_posix() for c in configs][:20],
        "config_count": len(configs),
        "aliases": aliases[:60],
        "alias_count": len(aliases),
        "resolvers": resolvers[:10],
        "drift": drift[:60],
        "drift_count": len(drift),
        "dead_targets": dead[:30],
        "dead_count": len(dead),
        "unbuilt_targets": unbuilt[:20],
        "unbuilt_count": len(unbuilt),
        "unused": unused[:30],
        "unused_count": len(unused),
        "resolver_only": resolver_only[:20],
        "bad_references": bad_refs[:20],
        "notes": notes[:20],
        "files_scanned": scanned,
        "scan_capped": capped,
        "not_checked": [
            "a bundler config is a program, not data: only the literal keys of an alias or moduleNameMapper block are read, and a block that spreads or computes its keys is reported as unreadable, never as empty",
            "an alias a plugin adds at run time — every mirroring plugin this play knows is named in the output, and one it does not know would read as drift",
            "whether an unused alias is dead or only reached from a file type this scan does not read",
            "a tsconfig outside the repository root against a build this play never saw — the resolver configs read here are the ones at the root, so only a root tsconfig is compared against them",
        ],
    }


def main():
    if len(sys.argv) != 2:
        print("usage: aliases.py <repo-dir>", file=sys.stderr)
        sys.exit(2)
    root = Path(sys.argv[1].strip())
    if not root.is_dir():
        print("aliases: not a directory: " + str(root), file=sys.stderr)
        sys.exit(1)
    json.dump(audit(root), sys.stdout, separators=(",", ":"))
    print()


if __name__ == "__main__":
    main()
