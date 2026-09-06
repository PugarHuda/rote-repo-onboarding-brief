#!/usr/bin/env python3
"""Self-check for probe.py. Run: python3 test_probe.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.parent / "resources"


def probe(root):
    p = subprocess.run([sys.executable, str(HERE / "probe.py"), str(root)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_node():
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        (r / "package.json").write_text(json.dumps({
            "name": "demo", "description": "a demo",
            "scripts": {"test": "vitest", "dev": "vite"},
            "dependencies": {"react": "^19"},
        }))
        (r / "README.md").write_text("# Demo\n\nDoes a thing.\n")
        (r / "test").mkdir()
        f = probe(r)
        assert f["ecosystems"] == ["node"], f["ecosystems"]
        assert f["node"]["scripts"]["dev"] == "vite"
        assert f["readme"]["first_paragraph"] == "Does a thing."
        assert f["test_dirs"] == ["test"]
        # No lockfile committed -> must be visible as a fact, not silently dropped.
        assert f["lockfiles_present"] == []


def test_python_and_secrets():
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        (r / "pyproject.toml").write_text(
            '[project]\nname="demo"\nrequires-python=">=3.11"\n'
            'dependencies=["httpx"]\n[tool.ruff]\n')
        (r / ".env").write_text("SECRET=x\n")
        f = probe(r)
        assert f["ecosystems"] == ["python"]
        assert f["python"]["requires_python"] == ">=3.11"
        assert "ruff" in f["python"]["tooling"]
        assert f["committed_secret_files"] == [".env"]


def test_shallow_clone_reports_no_history_count():
    """A --depth 1 clone must not report a commit count that looks real."""
    with tempfile.TemporaryDirectory() as d:
        origin, clone = Path(d) / "origin", Path(d) / "clone"
        origin.mkdir()
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
        for i in range(3):
            (origin / f"f{i}.txt").write_text(str(i))
            subprocess.run(["git", "add", "-A"], cwd=origin, check=True)
            subprocess.run(["git", "commit", "-qm", f"c{i}"], cwd=origin, check=True, env=env)
        subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(clone)],
                       check=True, capture_output=True)

        full = probe(origin)
        assert full["git"]["shallow_clone"] is False
        assert full["git"]["commit_count"] == "3", full["git"]

        sh = probe(clone)
        assert sh["git"]["shallow_clone"] is True
        assert "commit_count" not in sh["git"], sh["git"]
        assert "contributors" not in sh["git"], sh["git"]


def test_license_detection_is_case_and_extension_insensitive():
    """Regression: execa ships `license` (lowercase, no extension) and the brief
    wrongly reported 'No LICENSE file' while listing it in the layout."""
    for fname in ("license", "LICENSE", "LICENSE.md", "LICENCE.txt", "COPYING"):
        with tempfile.TemporaryDirectory() as d:
            r = Path(d)
            (r / "package.json").write_text('{"name":"x"}')
            (r / fname).write_text("MIT")
            assert probe(r)["has_license"] is True, fname
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        (r / "package.json").write_text('{"name":"x"}')
        assert probe(r)["has_license"] is False


def test_missing_dir_is_an_error_not_a_crash():
    p = subprocess.run([sys.executable, str(HERE / "probe.py"), "/nope/nope"],
                       capture_output=True, text=True)
    assert p.returncode == 1
    assert "error" in json.loads(p.stdout)


def test_local_only_files_are_named_as_a_stranger_trap():
    import os, shutil
    if not shutil.which("git"):
        print("skip: git not on PATH"); return
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x")
        g = lambda *a: subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True, env=env)
        g("init", "-q"); (r / "README.md").write_text("# x\n"); (r / ".gitignore").write_text(".env\n")
        g("add", "-A"); g("commit", "-q", "-m", "one")
        (r / ".env").write_text("SECRET=1\n"); (r / "helper.sh").write_text("echo\n")
        d = json.loads(subprocess.run([sys.executable, str(HERE / "probe.py"), str(r)], capture_output=True, text=True, timeout=60).stdout)
        lof = d["local_only_files"]
        assert lof["count"] == 2 and sorted(lof["sample"]) == [".env", "helper.sh"] and lof["secret_shaped"] == [".env"], lof


def test_declared_entry_points_and_missing_targets():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"name": "x", "main": "dist/index.js", "bin": {"x": "bin/x.js"}, "scripts": {"start": "node dist/index.js"}}))
        (r / "bin").mkdir(); (r / "bin" / "x.js").write_text("")
        (r / "pyproject.toml").write_text('[project]\nname = "p"\n[project.scripts]\np = "p.cli:main"\n')
        (r / "p").mkdir(); (r / "p" / "cli.py").write_text("")
        (r / "cmd" / "server").mkdir(parents=True); (r / "cmd" / "server" / "main.go").write_text("package main")
        d = json.loads(subprocess.run([sys.executable, str(HERE / "probe.py"), str(r)], capture_output=True, text=True, timeout=60).stdout)
        ep = {e["kind"]: e for e in d["entry_points"]}
        assert ep["package.json main"]["exists"] is False          # dist/ not built -> MISSING
        assert ep["package.json bin x"]["exists"] is True
        assert ep["pyproject [project.scripts] p"]["exists"] is True
        assert ep["go cmd"]["target"] == "cmd/server/main.go"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL PASS")
