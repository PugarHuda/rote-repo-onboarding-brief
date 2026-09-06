#!/usr/bin/env python3
"""Self-check for codeowners.py. Run: python3 test_codeowners.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.parent / "resources"


def run(root):
    p = subprocess.run([sys.executable, str(HERE / "codeowners.py"), str(root)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def touch(root, *paths):
    for p in paths:
        f = root / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")


def rule(d, pattern):
    return next(r for r in d["rules"] if r["pattern"] == pattern)


def test_absent_file_says_where_it_looked():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "src/a.py")
        d = run(r)
        assert d["is_present"] is False
        assert ".github/CODEOWNERS" in d["looked_for"]
        assert d["file_count"] == 1


def test_matching_semantics_and_statuses():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "README.md", "src/app.js", "src/deep/util.js", "docs/a.md",
              "docs/sub/b.md", "apps/web/index.ts", "lib/apps/x.ts", "infra/main.tf")
        (r / "CODEOWNERS").write_text("\n".join([
            "# comment",
            "*.js        @org/frontend",
            "docs/*      @writer",
            "/apps/      @org/apps",
            "src/deep/   @org/frontend",   # shadows nothing: it is the LAST match for util.js
            "*.js        @org/js-late",    # overrides *.js for every js file -> first *.js shadowed
            "gone/       @nobody",         # matches nothing
            "",
        ]))
        d = run(r)
        assert d["is_present"] and d["codeowners_file"] == "CODEOWNERS"
        # docs/* must not descend
        assert rule(d, "docs/*")["matched"] == 1
        # /apps/ is anchored: lib/apps/x.ts is not matched
        assert rule(d, "/apps/")["matched"] == 1
        # last match wins: first *.js is shadowed by the later *.js
        assert rule(d, "*.js")["status"] == "shadowed"
        assert d["rules"][-2]["pattern"] == "*.js" and d["rules"][-2]["status"] == "ok"
        assert rule(d, "gone/")["status"] == "matches nothing"
        assert [s["pattern"] for s in d["stale_rules"]] == ["gone/"]
        # src/deep/ is later than the first *.js but earlier than the last one,
        # so util.js belongs to @org/js-late; src/deep/ still owns nothing.
        assert rule(d, "src/deep/")["status"] == "shadowed"
        unowned = set(d["unowned_sample"])
        # CODEOWNERS itself is a tracked file, and nothing owns it.
        assert unowned == {"CODEOWNERS", "README.md", "docs/sub/b.md", "lib/apps/x.ts", "infra/main.tf"}
        assert d["owned_count"] == 4 and d["unowned_count"] == 5
        assert d["coverage_pct"] == 44.4
        owners = {o["owner"]: o["files"] for o in d["owners"]}
        assert owners == {"@org/js-late": 2, "@writer": 1, "@org/apps": 1}


def test_syntax_problems_and_precedence():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "a.py", "b.py")
        (r / ".github").mkdir()
        (r / ".github" / "CODEOWNERS").write_text("!a.py @x\n*.py notanowner\n[Section]\n*.py\n")
        (r / "CODEOWNERS").write_text("* @root-file\n")
        d = run(r)
        assert d["codeowners_file"] == ".github/CODEOWNERS"
        assert d["other_codeowners_files"] == ["CODEOWNERS"]
        kinds = sorted(p["kind"] for p in d["problems"])
        assert kinds == ["bad_owner", "bad_pattern", "gitlab_section"]
        # the final ownerless `*.py` rule clears ownership on purpose
        assert d["unowned_count"] == 4 and d["owners"] == []  # a.py, b.py, both CODEOWNERS files


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
