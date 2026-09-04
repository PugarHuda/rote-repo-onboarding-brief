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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    print("ALL PASS")
