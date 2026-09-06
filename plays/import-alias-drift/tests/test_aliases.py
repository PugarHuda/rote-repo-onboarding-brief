#!/usr/bin/env python3
"""Self-check for aliases.py. Run: python3 test_aliases.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.parent / "resources"


def run(root):
    p = subprocess.run([sys.executable, str(HERE / "aliases.py"), str(root)],
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def write(root, path, text):
    f = root / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")


def tsconfig(paths, base_url="."):
    return json.dumps({"compilerOptions": {"baseUrl": base_url, "paths": paths}})


def test_alias_missing_from_jest_is_drift_and_counts_the_tests_that_import_it():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        write(r, "tsconfig.json", tsconfig({"@/*": ["./src/*"], "@ui/*": ["./ui/*"]}))
        write(r, "src/a.ts", "export const a = 1\n")
        write(r, "ui/b.ts", "export const b = 2\n")
        write(r, "src/uses.ts", "import { a } from '@/a'\n")
        write(r, "src/__tests__/uses.spec.ts", "import { b } from '@ui/b'\nimport { a } from '@/a'\n")
        write(r, "jest.config.js", "module.exports = { moduleNameMapper: { '^@/(.*)$': '<rootDir>/src/$1' } }\n")
        d = run(r)
        drift = {x["alias"]: x for x in d["drift"]}
        # @/* is mapped by the regex key and must not be reported
        assert list(drift) == ["@ui/*"], d["drift"]
        assert drift["@ui/*"]["files"] == 1 and drift["@ui/*"]["test_files"] == 1, drift
        assert d["dead_count"] == 0 and d["unused"] == [], d


def test_a_mirroring_plugin_is_named_instead_of_reported_as_drift():
    """vite-tsconfig-paths makes vite read tsconfig, so there is nothing to drift from."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        write(r, "tsconfig.json", tsconfig({"@/*": ["./src/*"]}))
        write(r, "src/a.ts", "export const a = 1\n")
        write(r, "src/uses.ts", "import { a } from '@/a'\n")
        write(r, "vite.config.ts", "import tsconfigPaths from 'vite-tsconfig-paths'\n"
                                   "export default { plugins: [tsconfigPaths()] }\n")
        d = run(r)
        assert d["drift_count"] == 0, d["drift"]
        vite = next(x for x in d["resolvers"] if x["resolver"] == "vite")
        assert "vite-tsconfig-paths" in vite["mirrored"], vite


def test_a_computed_alias_block_reads_as_unreadable_not_as_empty():
    """A config that spreads its aliases in has not told us it lacks them."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        write(r, "tsconfig.json", tsconfig({"@/*": ["./src/*"]}))
        write(r, "src/a.ts", "export const a = 1\n")
        write(r, "src/uses.ts", "import { a } from '@/a'\n")
        write(r, "vite.config.ts", "import { aliases } from './scripts/aliases'\n"
                                   "export default { resolve: { alias: { ...aliases } } }\n")
        d = run(r)
        vite = next(x for x in d["resolvers"] if x["resolver"] == "vite")
        assert vite["unreadable"] is True and vite["keys"] == [], vite
        assert d["drift_count"] == 0, d["drift"]


def test_a_target_that_is_built_or_installed_is_not_called_dead():
    """lib/ and node_modules/ are absent from a fresh checkout by design."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        write(r, "tsconfig.json", tsconfig({
            "@gone/*": ["./packages/gone/src/*"],
            "@built": ["./packages/core/lib/index.d.ts"],
            "react": ["./node_modules/preact/compat/"],
        }))
        d = run(r)
        assert [a["alias"] for a in d["dead_targets"]] == ["@gone/*"], d["dead_targets"]
        assert sorted(a["alias"] for a in d["unbuilt_targets"]) == ["@built", "react"], d["unbuilt_targets"]


def test_tsconfig_comments_trailing_commas_and_a_missing_extends():
    """tsconfig.json is JSON with comments in practice, and a URL's // is not one."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        write(r, "tsconfig.json", """{
  // see https://www.typescriptlang.org/tsconfig
  "extends": "./tsconfig.base.json",
  "compilerOptions": {
    "baseUrl": ".",
    "paths": {
      "@/*": ["./src/*"],
    },
  },
  "references": [{ "path": "./packages/gone" }],
}
""")
        write(r, "src/a.ts", "export const a = 1\n")
        d = run(r)
        assert [a["alias"] for a in d["aliases"]] == ["@/*"], d["aliases"]
        assert any("tsconfig.base.json" in n for n in d["notes"]), d["notes"]
        assert [x["path"] for x in d["bad_references"]] == ["./packages/gone"], d["bad_references"]


def test_a_nested_tsconfig_is_not_measured_against_the_root_bundler():
    """A tsconfig inside a test corpus answers to a build this play never saw."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        write(r, "playground/fixture/tsconfig.json", tsconfig({"#x/*": ["./src/*"]}))
        write(r, "playground/fixture/src/a.ts", "export const a = 1\n")
        write(r, "vite.config.ts", "export default { resolve: { alias: { 'vite/runner': './x' } } }\n")
        d = run(r)
        assert d["alias_count"] == 1 and d["aliases"][0]["at_root"] is False, d["aliases"]
        assert d["drift_count"] == 0, d["drift"]
        assert [x["alias"] for x in d["resolver_only"]] == ["vite/runner"], d["resolver_only"]


def test_asset_stubs_are_not_mistaken_for_aliases():
    """`\\.(css|sass)$` maps an asset to a mock; it is not a module path."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        write(r, "tsconfig.json", tsconfig({"@/*": ["./src/*"]}))
        write(r, "src/a.ts", "export const a = 1\n")
        write(r, "src/uses.ts", "import { a } from '@/a'\n")
        write(r, "jest.config.cjs", "module.exports = { moduleNameMapper: {"
                                    " '\\\\.(css|sass|scss)$': 'identity-obj-proxy',"
                                    " '^@/(.*)$': '<rootDir>/src/$1',"
                                    " '^@/test$': '<rootDir>/test/index.ts' } }\n")
        d = run(r)
        # the asset stub is not an alias, and @/test is already covered by @/*
        assert d["resolver_only"] == [], d["resolver_only"]
        assert d["drift_count"] == 0, d["drift"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
