"""Walk a repository and yield source files worth weaving into the graph."""

from __future__ import annotations

import os
from pathlib import Path

SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".graphloom",
    ".venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "venv",
    "env",
    "dist",
    "build",
    "site-packages",
    "coverage",
}

SKIP_FILE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "npm-shrinkwrap.json",
}

PY_SUFFIXES = {".py"}
JS_SUFFIXES = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}
SQL_SUFFIXES = {".sql"}
MD_SUFFIXES = {".md", ".markdown"}
CONFIG_SUFFIXES = {".toml", ".yaml", ".yml", ".json"}
MAX_BYTES = 512_000
MAX_LINE = 10_000


def interesting(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in PY_SUFFIXES | JS_SUFFIXES | SQL_SUFFIXES | MD_SUFFIXES | CONFIG_SUFFIXES


def iter_source_files(root: Path) -> list[Path]:
    """Return sorted source files under `root`, skipping vendor and hidden trees."""
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if name not in SKIP_DIR_NAMES
            and not name.startswith(".")
            and not name.endswith(".egg-info")
        ]
        for name in sorted(filenames):
            if name.startswith(".") or name in SKIP_FILE_NAMES:
                continue
            path = Path(dirpath) / name
            if not interesting(path):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_BYTES or size == 0:
                continue
            found.append(path)
    return found


def read_text(path: Path) -> str | None:
    """Read UTF-8 text. Return None when a line looks minified."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if len(line) > MAX_LINE:
            return None
    return text


def rel_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
