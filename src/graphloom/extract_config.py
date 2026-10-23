"""Config key nodes for a practical subset of TOML, YAML, and JSON.

The parsers are intentionally small. They cover nested maps and scalar
assignments, which is what a code reference such as settings["smtp"]["host"]
needs in order to land on a node.
"""

from __future__ import annotations

import json
import re

from graphloom.model import Graph, Node

_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_TOML_SECTION = re.compile(r"^\[([A-Za-z0-9_.-]+)\]\s*(?:#.*)?$")
_TOML_ASSIGN = re.compile(r"^([A-Za-z0-9_-]+)\s*=\s*(.+?)\s*(?:#.*)?$")
_YAML_KEY = re.compile(r"^(\s*)([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*(?:#.*)?$")


def extract_config(graph: Graph, rel: str, source: str) -> None:
    suffix = rel.rsplit(".", 1)[-1].lower()
    if suffix == "toml":
        pairs = list(parse_toml_keys(source))
        language = "toml"
    elif suffix in {"yaml", "yml"}:
        pairs = list(parse_yaml_keys(source))
        language = "yaml"
    elif suffix == "json":
        pairs = list(parse_json_keys(source))
        language = "json"
    else:
        return
    for dotted, line in pairs[:200]:
        if not dotted or len(dotted) > 120:
            continue
        graph.add_node(
            Node(
                id=f"cfg:{rel}:{dotted}",
                kind="config",
                name=dotted,
                layer="config",
                language=language,
                file=rel,
                line=line,
                end_line=line,
                detail={"key": dotted, "role": "config"},
            )
        )


def parse_toml_keys(source: str) -> list[tuple[str, int]]:
    """Bare keys, `[section]` headers, and one level of dotted sections."""
    found: list[tuple[str, int]] = []
    prefix = ""
    for index, raw in enumerate(source.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        section = _TOML_SECTION.match(line)
        if section:
            prefix = section.group(1)
            found.append((prefix, index))
            continue
        assign = _TOML_ASSIGN.match(line)
        if not assign:
            continue
        key = assign.group(1)
        dotted = f"{prefix}.{key}" if prefix else key
        found.append((dotted, index))
    return found


def parse_yaml_keys(source: str) -> list[tuple[str, int]]:
    """Indentation-sensitive `key:` maps. Lists and multiline blocks are skipped."""
    found: list[tuple[str, int]] = []
    stack: list[tuple[int, str]] = []
    for index, raw in enumerate(source.splitlines(), start=1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw[: len(raw) - len(raw.lstrip(" \t"))]:
            continue
        match = _YAML_KEY.match(raw)
        if not match:
            continue
        indent = len(match.group(1).replace("\t", "  "))
        key = match.group(2)
        value = match.group(3).strip()
        while stack and stack[-1][0] >= indent:
            stack.pop()
        stack.append((indent, key))
        dotted = ".".join(item[1] for item in stack)
        if dotted.count(".") <= 5:
            found.append((dotted, index))
        if value not in {"", "|", ">", "|-", ">-", "|+", ">+"}:
            stack.pop()
    return found


def parse_json_keys(source: str) -> list[tuple[str, int]]:
    try:
        data = json.loads(source)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    lines = source.splitlines()
    found: list[tuple[str, int]] = []

    def walk(obj: dict, prefix: str, depth: int) -> None:
        if depth > 6 or len(found) >= 200:
            return
        for key, value in obj.items():
            if not isinstance(key, str) or not key:
                continue
            dotted = f"{prefix}.{key}" if prefix else key
            found.append((dotted, _json_line(lines, key)))
            if isinstance(value, dict):
                walk(value, dotted, depth + 1)

    walk(data, "", 0)
    return found


def _json_line(lines: list[str], key: str) -> int:
    pattern = re.compile(rf'["\']{re.escape(key)}["\']\s*:')
    for index, line in enumerate(lines, start=1):
        if pattern.search(line):
            return index
    return 1


def valid_key(key: str) -> bool:
    return bool(_KEY.match(key.split(".")[-1]))
