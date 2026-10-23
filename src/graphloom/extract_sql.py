"""SQL extractor: CREATE TABLE nodes and foreign-key edges."""

from __future__ import annotations

import re

from graphloom.model import Evidence, Graph, Node, RawRef
from graphloom.util import excerpt_at

_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:(?:\w+)\.)?[\"'`]?"
    r"([A-Za-z_][\w$]*)[\"'`]?",
    re.IGNORECASE,
)
_REF = re.compile(
    r"\bREFERENCES\s+[\"'`]?([A-Za-z_][\w$]*)[\"'`]?",
    re.IGNORECASE,
)
_SKIP_PREFIX = ("CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "INDEX", "KEY")


def extract_sql(graph: Graph, refs: list[RawRef], rel: str, source: str) -> None:
    lines = source.splitlines()
    script_id = f"sql:{rel}"
    graph.add_node(
        Node(
            id=script_id,
            kind="sqlscript",
            name=rel,
            layer="data",
            language="sql",
            file=rel,
            line=1,
            end_line=max(1, len(lines)),
            detail={"role": "sqlscript"},
        )
    )
    for match in _CREATE.finditer(source):
        name = match.group(1)
        block, block_end = _paren_block(source, match.end())
        line = source[: match.start()].count("\n") + 1
        end_line = source[:block_end].count("\n") + 1 if block else line
        columns, targets = _columns_and_refs(block)
        table_id = f"tbl:{name.lower()}"
        graph.add_node(
            Node(
                id=table_id,
                kind="table",
                name=name,
                layer="data",
                language="sql",
                file=rel,
                line=line,
                end_line=end_line,
                detail={"columns": columns, "role": "table"},
            )
        )
        graph.add_edge(
            script_id,
            table_id,
            "contains",
            "parsed",
            [Evidence(rel, line, excerpt_at(lines, line))],
        )
        for target in targets:
            refs.append(
                RawRef(
                    kind="fk",
                    source=table_id,
                    name=target,
                    file=rel,
                    line=line,
                    excerpt=excerpt_at(lines, line),
                )
            )


def _paren_block(source: str, start: int) -> tuple[str, int]:
    index = source.find("(", start)
    if index < 0:
        return "", start
    depth = 0
    in_quote: str | None = None
    for cursor in range(index, len(source)):
        char = source[cursor]
        if in_quote:
            if char == in_quote and source[cursor - 1] != "\\":
                in_quote = None
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return source[index + 1 : cursor], cursor
    return source[index + 1 :], len(source)


def _columns_and_refs(block: str) -> tuple[list[str], list[str]]:
    if not block.strip():
        return [], []
    columns: list[str] = []
    targets: list[str] = []
    for part in _split_commas(block):
        targets.extend(_REF.findall(part))
        stripped = part.strip()
        upper = stripped.upper()
        if not stripped or upper.startswith(_SKIP_PREFIX):
            continue
        col = re.match(r"[\"'`]?([A-Za-z_][\w$]*)[\"'`]?", stripped)
        if col:
            columns.append(col.group(1))
    # Preserve order while dropping duplicate FK targets.
    seen: set[str] = set()
    unique_targets: list[str] = []
    for target in targets:
        key = target.lower()
        if key in seen:
            continue
        seen.add(key)
        unique_targets.append(target)
    return columns, unique_targets


def _split_commas(block: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_quote: str | None = None
    start = 0
    for index, char in enumerate(block):
        if in_quote:
            if char == in_quote and (index == 0 or block[index - 1] != "\\"):
                in_quote = None
            continue
        if char in {"'", '"', "`"}:
            in_quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(block[start:index])
            start = index + 1
    parts.append(block[start:])
    return parts
