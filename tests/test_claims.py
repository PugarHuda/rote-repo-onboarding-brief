#!/usr/bin/env python3
"""Self-check for claims.py. Run: python3 test_claims.py"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent.parent / "resources"


def claims(root):
    p = subprocess.run([sys.executable, str(HERE / "claims.py"), str(root)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def by_cmd(d):
    return {c["command"]: c["status"] for c in d["claims"]}


def test_npm_script_defined_vs_doc_rot():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"scripts": {"build": "tsc"}}))
        (r / "README.md").write_text(
            "# x\n```bash\nnpm run build\nnpm run deploy\nnpm install\n```\n")
        s = by_cmd(claims(r))
        assert s["npm run build"] == "defined", s
        # Documented but never defined -- this is the finding that matters.
        assert s["npm run deploy"] == "undefined", s
        assert s["npm install"] == "unknown", s


def test_justfile_recipe_with_default_params_is_not_a_false_positive():
    """Regression: `harness harness="codex":` is a real recipe, not doc rot."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "justfile").write_text(
            'export FOO := "bar"\n'
            'harness harness="codex":\n\techo hi\n\n'
            'release-publish release_repo="../rote-releases":\n\techo hi\n\n'
            'status:\n\techo hi\n')
        (r / "README.md").write_text(
            "```sh\njust harness codex\njust release-publish\njust status\njust nope\n```\n")
        d = claims(r)
        assert set(d["just_recipes"]) == {"harness", "release-publish", "status"}, d["just_recipes"]
        s = by_cmd(d)
        for cmd in ("just harness codex", "just release-publish", "just status"):
            assert s[cmd] in ("defined", "tool_missing"), (cmd, s[cmd])
        assert s["just nope"] == "undefined", s


def test_makefile_targets():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "Makefile").write_text("CC := gcc\n\ntest: build\n\techo hi\n\nbuild:\n\techo hi\n")
        (r / "README.md").write_text("```\nmake test\nmake ghost\n```\n")
        d = claims(r)
        assert "CC" not in d["make_targets"], d["make_targets"]
        s = by_cmd(d)
        assert s["make test"] in ("defined", "tool_missing"), s
        assert s["make ghost"] == "undefined", s


def test_prompts_and_non_shell_fences_are_handled():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"scripts": {"dev": "vite"}}))
        (r / "README.md").write_text(
            "```console\n$ npm run dev\n```\n"
            "```python\nnpm run should_be_ignored\n```\n")
        s = by_cmd(claims(r))
        assert "npm run dev" in s, s          # `$ ` prompt stripped
        assert "npm run should_be_ignored" not in s, s   # python fence ignored


def test_cargo_go_python_docker_and_task_claims():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "Cargo.toml").write_text('[package]\nname = "app"\n[[bin]]\nname = "tool"\n')
        (r / "go.mod").write_text("module x\n")
        (r / "cmd" / "server").mkdir(parents=True)
        (r / "pyproject.toml").write_text('[project]\nname = "p"\n[project.scripts]\nserve = "p:main"\n')
        (r / "tox.ini").write_text("[tox]\nenvlist = py312, lint\n")
        (r / "noxfile.py").write_text("import nox\n@nox.session\ndef tests(session):\n    pass\n")
        (r / "pkg").mkdir(); (r / "pkg" / "__init__.py").write_text("")
        (r / "docker-compose.yml").write_text("services:\n  web:\n    image: x\n  db:\n    image: y\n")
        (r / "Taskfile.yml").write_text("version: '3'\ntasks:\n  lint:\n    cmds: [echo]\n")
        (r / "Dockerfile").write_text("FROM scratch\n")
        (r / "package.json").write_text(json.dumps({"devDependencies": {"vitest": "^1"}}))
        (r / "README.md").write_text("```sh\n" + "\n".join([
            "cargo run --bin tool", "cargo run --bin nope", "cargo test",
            "go run ./cmd/server", "go run ./cmd/gone",
            "poetry run serve", "poetry run missing", "tox -e lint", "tox -e py99",
            "nox -s tests", "nox -s bench", "python -m pkg", "python -m nothere",
            "docker compose up web", "docker compose up cache", "docker build -t x .",
            "task lint", "task deploy", "npx vitest", "npx some-random-cli",
            "python hello.py --count=3", "python scripts/gone.py", "pytest tests/nope",
        ]) + "\n```\n")
        s = by_cmd(claims(r))
        assert s["cargo run --bin tool"] == "defined" or s["cargo run --bin tool"] == "tool_missing", s
        assert s["cargo run --bin nope"] == "undefined"
        assert s["go run ./cmd/gone"] == "undefined" and s["go run ./cmd/server"] in ("defined", "tool_missing")
        assert s["poetry run serve"] in ("defined", "tool_missing") and s["poetry run missing"] == "undefined"
        assert s["tox -e lint"] in ("defined", "tool_missing") and s["tox -e py99"] == "undefined"
        assert s["nox -s tests"] in ("defined", "tool_missing") and s["nox -s bench"] == "undefined"
        assert s["python -m pkg"] in ("defined", "tool_missing") and s["python -m nothere"] == "undefined"
        assert s["docker compose up web"] in ("defined", "tool_missing") and s["docker compose up cache"] == "undefined"
        assert s["docker build -t x ."] in ("defined", "tool_missing")
        assert s["task lint"] in ("defined", "tool_missing") and s["task deploy"] == "undefined"
        assert s["npx vitest"] in ("defined", "tool_missing") and s["npx some-random-cli"] == "unknown"
        # a bare example file the reader writes is not doc rot; a missing repo path is
        assert s["python hello.py --count=3"] == "unknown"
        assert s["python scripts/gone.py"] == "undefined" and s["pytest tests/nope"] == "undefined"


def test_toolchain_floors_against_this_machine_offline():
    import os
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"engines": {"node": ">=20"}, "packageManager": "pnpm@9.1.0"}))
        (r / ".nvmrc").write_text("22\n")
        (r / "pyproject.toml").write_text('[project]\nname="p"\nrequires-python = ">=3.11"\n')
        (r / "go.mod").write_text("module x\n\ngo 1.22\n")
        (r / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.75"\n')
        (r / "README.md").write_text("")
        fx = r / "versions.json"
        fx.write_text(json.dumps({"node": "18.19.0", "python3": "3.12.1", "go": "1.21.5", "cargo": "1.80.0"}))
        env = dict(os.environ, CLAIMS_TOOL_VERSIONS=str(fx))
        p = subprocess.run([sys.executable, str(HERE / "claims.py"), str(r)], capture_output=True, text=True, timeout=60, env=env)
        assert p.returncode == 0, p.stderr
        rows = {(x["tool"], x["source"]): x["status"] for x in json.loads(p.stdout)["toolchain"]}
        assert rows[("node", "package.json engines.node")] == "below_floor"   # 18 < 20
        assert rows[("node", ".nvmrc")] == "below_floor"                       # 18 != 22.x
        assert rows[("pnpm", "package.json packageManager")] == "missing"      # not in fixture
        assert rows[("python3", "pyproject requires-python")] == "ok"
        assert rows[("go", "go.mod go directive")] == "below_floor"            # 1.21 < 1.22
        assert rows[("cargo", "rust-toolchain.toml")] == "ok"                  # 1.80 >= 1.75


def test_satisfies_ranges():
    sys.path.insert(0, str(HERE))
    from claims import satisfies
    assert satisfies(">=18 <21", "20.5.0") is True and satisfies(">=18 <21", "21.0.0") is False
    assert satisfies("^18.0.0 || ^20.0.0", "20.11.0") is True and satisfies("^18.0.0 || ^20.0.0", "19.0.0") is False
    assert satisfies("20.x", "20.3.1") is True and satisfies("20.x", "21.0.0") is False
    assert satisfies("~3.11", "3.11.9") is True and satisfies("~3.11", "3.12.0") is False
    assert satisfies("1.22", "1.22.0") is True and satisfies("1.22", "1.21.9") is False
    assert satisfies("lts/*", "22.0.0") is True and satisfies("weird", "1.0.0") is None


def test_docs_lines_env_and_first_run():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"scripts": {"dev": "vite", "test": "vitest"}}))
        (r / "package-lock.json").write_text("{}")
        (r / "README.md").write_text("# x\n\nintro\n\n```sh\nnpm run dev\n```\n")
        (r / "CONTRIBUTING.md").write_text("```bash\nnpm run lint\n```\n")
        (r / "docs").mkdir(); (r / "docs" / "guide.md").write_text("```\nnpm run build\n```\n")
        (r / ".env.example").write_text("DATABASE_URL=postgres://x\nUNUSED_KEY=1\n")
        (r / "src").mkdir()
        (r / "src" / "app.js").write_text(
            'const a = process.env.DATABASE_URL;\nconst b = process.env.SECRET_TOKEN;\n'
            'const c = process.env.LOG_LEVEL || "info";\nconst d = process.env.NODE_ENV;\n')
        (r / "src" / "cfg.py").write_text('x = os.environ["API_KEY"]\ny = os.getenv("REGION", "eu")\n')
        d = claims(r)
        assert d["docs_scanned"] == ["README.md", "CONTRIBUTING.md", "docs/guide.md"]
        by = {c["command"]: c for c in d["claims"]}
        assert by["npm run dev"]["source"] == "README.md" and by["npm run dev"]["line"] == 6
        assert by["npm run lint"]["source"] == "CONTRIBUTING.md" and by["npm run lint"]["status"] == "undefined"
        assert by["npm run build"]["source"] == "docs/guide.md"
        env = d["env"]
        assert env["read_count"] == 5  # NODE_ENV is noise
        assert [e["name"] for e in env["undocumented_required"]] == ["API_KEY", "SECRET_TOKEN"]
        assert env["undocumented_with_fallback"] == ["LOG_LEVEL", "REGION"]
        assert env["documented_never_read"] == ["UNUSED_KEY"]
        steps = [(s["step"], s["command"]) for s in d["first_run"]]
        assert steps[0] == ("install", "npm ci") and ("run", "npm run dev") in steps and ("test", "npm test") in steps


def test_floor_conflicts_lockfile_conflict_and_broken_doc_links():
    import os
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"engines": {"node": ">=20"}}))
        (r / ".nvmrc").write_text("18\n")                       # contradicts engines
        (r / "package-lock.json").write_text("{}"); (r / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
        (r / "docs").mkdir(); (r / "docs" / "real.md").write_text("x")
        (r / "README.md").write_text("[guide](docs/real.md) [gone](docs/missing.md) ![img](assets/logo.png) [ext](https://x.y/z)\n")
        fx = r / "v.json"; fx.write_text(json.dumps({"node": "20.1.0"}))
        env = dict(os.environ, CLAIMS_TOOL_VERSIONS=str(fx))
        p = subprocess.run([sys.executable, str(HERE / "claims.py"), str(r)], capture_output=True, text=True, timeout=60, env=env)
        assert p.returncode == 0, p.stderr
        d = json.loads(p.stdout)
        assert d["floor_conflicts"] == [{"tool": "node", "a": ".nvmrc says 18", "b": "package.json engines.node says >=20"}], d["floor_conflicts"]
        assert d["lockfile_conflict"]["lockfiles"] == ["package-lock.json", "pnpm-lock.yaml"] and "no packageManager" in d["lockfile_conflict"]["decided_by"]
        dl = d["doc_links"]
        assert dl["checked"] == 3 and [b["target"] for b in dl["broken"]] == ["docs/missing.md", "assets/logo.png"]


def test_ci_and_dockerfile_floors_join_the_contradiction_check():
    import os
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "package.json").write_text(json.dumps({"engines": {"node": ">=20"}}))
        (r / ".github" / "workflows").mkdir(parents=True)
        (r / ".github" / "workflows" / "ci.yml").write_text("jobs:\n  test:\n    strategy:\n      matrix:\n        node-version: [18, 20]\n    steps:\n      - uses: actions/setup-node@v4\n        with:\n          node-version: ${{ matrix.node-version }}\n")
        (r / "Dockerfile").write_text("FROM node:18-alpine\n")
        (r / "README.md").write_text("")
        fx = r / "v.json"; fx.write_text(json.dumps({"node": "20.1.0"}))
        env = dict(os.environ, CLAIMS_TOOL_VERSIONS=str(fx))
        p = subprocess.run([sys.executable, str(HERE / "claims.py"), str(r)], capture_output=True, text=True, timeout=60, env=env)
        d = json.loads(p.stdout)
        srcs = sorted((x["source"], x["declared"]) for x in d["toolchain"])
        # the matrix collapses to its lowest version, one row per workflow
        assert ("ci: ci.yml (tests 2 versions, 18.x to 20.x)", "18.x") in srcs and ("Dockerfile FROM", "18.x") in srcs, srcs
        assert len([x for x in d["toolchain"] if x["source"].startswith("ci:")]) == 1
        conflicts = {(c["a"], c["b"]) for c in d["floor_conflicts"]}
        assert ("ci: ci.yml (tests 2 versions, 18.x to 20.x) says 18.x", "package.json engines.node says >=20") in conflicts, conflicts
        assert ("Dockerfile FROM says 18.x", "package.json engines.node says >=20") in conflicts, conflicts


def test_myst_directive_fences_do_not_turn_prose_into_commands():
    """Regression, found on pallets/click: MyST ```{eval-rst} fences left the old
    regex pairing every closing fence with the next opening one, so the prose
    between two real code blocks was scanned for commands and a sentence
    starting with the word "make" was reported as a missing Makefile target.
    The real command in the last block was missed at the same time."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        (r / "Makefile").write_text("build:\n\techo hi\n")
        (r / "README.md").write_text(
            "```{eval-rst}\n.. note:: hi\n```\n\n"
            "```python\nprint(1)\n```\n\n"
            "This limitation is unlikely to change because it would\n"
            "make resource handling much more complicated.\n\n"
            "```sh\nmake build\n```\n")
        s = by_cmd(claims(r))
        assert "make build" in s, s
        assert not any(c.startswith("make resource") for c in s), s


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL PASS")
