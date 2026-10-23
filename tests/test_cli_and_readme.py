"""CLI smoke test and the README phrases the project promises to keep."""

import os
import subprocess
import sys

from graphloom.llm import DEFAULT_BASE_URL, DEFAULT_MODEL
from paths import EXAMPLE, ROOT


def test_defaults_point_at_atmorouter():
    assert DEFAULT_BASE_URL == "https://atmorouter.dev/v1"
    assert DEFAULT_MODEL == "zai/glm-5.3-flash"


def test_readme_credits_atmorouter_and_names_the_gateway():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "cheap AI API gateway" in readme
    assert "OpenRouter alternative" in readme
    assert "Powered by [AtmoRouter](https://atmorouter.dev)." in readme
    assert "MIT" in readme
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "ATMOROUTER_API_KEY=ar-your-key-here" in env


def test_cli_build_query_and_path(tmp_path):
    env = os.environ.copy()
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    out = tmp_path / "out"
    build = subprocess.run(
        [sys.executable, "-m", "graphloom", "build", str(EXAMPLE), "--out", str(out)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert build.returncode == 0, build.stderr
    assert "nodes:" in build.stdout
    assert "parsed:" in build.stdout
    assert "linked:" in build.stdout
    assert (out / "graph.json").is_file()
    assert (out / "REPORT.md").is_file()
    assert (out / "graph.html").is_file()
    query = subprocess.run(
        [sys.executable, "-m", "graphloom", "query", "invoices", "--graph", str(out / "graph.json")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert query.returncode == 0, query.stderr
    assert "tbl:invoices" in query.stdout
    path = subprocess.run(
        [
            sys.executable,
            "-m",
            "graphloom",
            "path",
            "post_invoice",
            "send_receipt",
            "--graph",
            str(out / "graph.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert path.returncode == 0, path.stderr
    assert "hops: 4" in path.stdout
    missing = subprocess.run(
        [sys.executable, "-m", "graphloom", "query", "no-such-symbol", "--graph", str(out / "graph.json")],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert missing.returncode == 1
