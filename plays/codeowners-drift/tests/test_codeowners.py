#!/usr/bin/env python3
"""Self-check for codeowners.py. Run: python3 test_codeowners.py"""
import json
import os
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


def test_huge_rule_files_stay_under_rotes_64kb_stdout_cap():
    # Regression: home-assistant/core has 2,183 rules; the uncapped JSON blew past
    # rote's 65536-byte stdout capture and the presentation saw a truncated body.
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "src/keep.py")
        body = "\n".join(f"/gone/dir{i}/  @org/team{i}" for i in range(2500)) + "\nsrc/ @org/src\n"
        (r / "CODEOWNERS").write_text(body)
        p = subprocess.run([sys.executable, str(HERE / "codeowners.py"), str(r)],
                           capture_output=True, text=True, timeout=120)
        assert p.returncode == 0, p.stderr
        assert len(p.stdout.encode()) < 65536, len(p.stdout)
        d = json.loads(p.stdout)
        assert d["rule_count"] == 2501 and len(d["rules"]) == 150 and d["rules_omitted"] == 2351
        assert d["stale_count"] == 2500 and len(d["stale_rules"]) == 60
        assert d["coverage_pct"] == 50.0  # src/keep.py owned, CODEOWNERS itself not


def test_gitlab_sections_are_evaluated_independently():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "app/main.py", "docs/guide.md", "lib/x.py")
        (r / ".gitlab").mkdir()
        (r / ".gitlab" / "CODEOWNERS").write_text("\n".join([
            "[Backend] @backend",
            "app/            ",            # inherits @backend
            "lib/  @lib-team",
            "^[Docs][2] @writers",         # optional section, 2 approvals
            "docs/",
            "[Everything]",
            "*  @qa",                      # last match in ITS section; does not shadow other sections
            "",
        ]))
        d = run(r)
        assert d["forge"] == "gitlab" and d["codeowners_file"] == ".gitlab/CODEOWNERS"
        names = [s["name"] for s in d["sections"]]
        assert names == ["Backend", "Docs", "Everything"]
        docs = next(s for s in d["sections"] if s["name"] == "Docs")
        assert docs["optional"] is True and docs["approvals"] == 2 and docs["default_owners"] == ["@writers"]
        # every rule still owns something: sections do not override each other
        assert d["shadowed_count"] == 0 and d["stale_count"] == 0
        assert rule(d, "app/")["owners"] == ["@backend"] and rule(d, "docs/")["owners"] == ["@writers"]
        # docs/guide.md is owned by an optional section AND by [Everything]'s required rule -> required
        assert d["optional_only_count"] == 0
        assert d["unowned_count"] == 0 and d["coverage_pct"] == 100.0
        owners = {o["owner"]: o["files"] for o in d["owners"]}
        assert owners == {"@qa": 4, "@backend": 1, "@lib-team": 1, "@writers": 1}


def test_owner_verification_uses_api_status_and_never_claims_teams():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        touch(r, "a.py")
        (r / "CODEOWNERS").write_text("* @octocat @ghost-user-that-does-not-exist @acme/backend @nope-org/x dev@example.com\n")
        fx = r / "api.json"
        fx.write_text(json.dumps({"users/octocat": 200, "users/ghost-user-that-does-not-exist": 404,
                                  "orgs/acme": 200, "orgs/nope-org": 404}))
        env = dict(os.environ, CODEOWNERS_API_FIXTURES=str(fx))
        p = subprocess.run([sys.executable, str(HERE / "codeowners.py"), str(r), "verify_owners=true"],
                           capture_output=True, text=True, timeout=60, env=env)
        assert p.returncode == 0, p.stderr
        d = json.loads(p.stdout)
        st = {c["owner"]: c["status"] for c in d["owner_check"]}
        assert st == {"@octocat": "exists", "@ghost-user-that-does-not-exist": "missing",
                      "@acme/backend": "org_exists", "@nope-org/x": "missing", "dev@example.com": "skipped"}
        assert "needs an authenticated token" in next(c for c in d["owner_check"] if c["owner"] == "@acme/backend")["why"]
        # without the flag nothing is fetched and the output says how to turn it on
        d0 = run(r)
        assert d0["owner_check"] is None and "verify_owners=true" in d0["not_checked"][0]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok  ", t.__name__)
    print(f"{len(tests)} passed")
