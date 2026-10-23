"""Small helpers shared by extractors and the query layer."""

from __future__ import annotations

import re

SCHEMA = "graphloom.graph/v1"
PROVENANCE = ("parsed", "linked", "llm")
PROVENANCE_RANK = {"llm": 1, "linked": 2, "parsed": 3}

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def excerpt_at(lines: list[str], line: int, limit: int = 160) -> str:
    """Return a single trimmed source line, clipped to `limit` characters."""
    if line < 1 or line > len(lines):
        return ""
    text = lines[line - 1].strip()
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def slugify(text: str, limit: int = 48) -> str:
    """Lowercase a label into a stable id fragment."""
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    if len(slug) > limit:
        slug = slug[:limit].rstrip("-")
    return slug or "item"


def norm_http_path(path: str) -> str:
    """Drop a query string and a trailing slash so route strings compare."""
    path = path.split("?", 1)[0].strip()
    if len(path) > 1:
        path = path.rstrip("/")
    return path


def looks_like_http_method(value: str) -> bool:
    return value.upper() in _HTTP_METHODS


def estimate_tokens(text: str) -> int:
    """Rough token count. Four characters is a stable offline estimate."""
    return max(1, (len(text) + 3) // 4) if text else 0


def dotted_module(rel_posix: str) -> str:
    """Map `pkg/mod.py` to the dotted module name `pkg.mod`."""
    parts = rel_posix.split("/")
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        stem = parts[-1]
        if stem.endswith(".py"):
            parts[-1] = stem[:-3]
    return ".".join(p for p in parts if p)


def absolute_import(rel_posix: str, level: int, module: str | None) -> str:
    """Resolve a Python import, including relative levels, to a dotted name.

    `level` follows the language: 0 is absolute, 1 is the current package,
    2 is the parent package.
    """
    if level <= 0:
        return module or ""
    parts = rel_posix.split("/")
    package = parts[:-1]
    drop = level - 1
    if drop >= len(package):
        base: list[str] = []
    else:
        base = package[: len(package) - drop]
    extra = module.split(".") if module else []
    return ".".join([*base, *extra])
