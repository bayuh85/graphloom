"""Markdown extractor: one node per heading, plus backtick mention refs."""

from __future__ import annotations

import re

from graphloom.model import Evidence, Graph, Node, RawRef
from graphloom.util import excerpt_at, slugify

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_TICK = re.compile(r"`([^`\n]+)`")


def extract_markdown(graph: Graph, refs: list[RawRef], rel: str, source: str) -> None:
    lines = source.splitlines()
    doc_id = f"doc:{rel}"
    graph.add_node(
        Node(
            id=doc_id,
            kind="document",
            name=rel,
            layer="docs",
            language="markdown",
            file=rel,
            line=1,
            end_line=max(1, len(lines)),
            detail={"role": "document"},
        )
    )
    headings = _headings(lines)
    used: dict[str, int] = {}
    for index, (level, title, line) in enumerate(headings):
        # Stop at the next heading of any level so a parent does not also
        # claim the backticks that belong to its children.
        if index + 1 < len(headings):
            end = headings[index + 1][2] - 1
        else:
            end = len(lines)
        slug = slugify(title)
        used[slug] = used.get(slug, 0) + 1
        if used[slug] > 1:
            slug = f"{slug}-{used[slug]}"
        hid = f"doc:{rel}:h:{slug}"
        graph.add_node(
            Node(
                id=hid,
                kind="heading",
                name=title,
                layer="docs",
                language="markdown",
                file=rel,
                line=line,
                end_line=max(line, end),
                detail={"level": level, "slug": slug, "role": "heading"},
            )
        )
        graph.add_edge(
            doc_id,
            hid,
            "contains",
            "parsed",
            [Evidence(rel, line, excerpt_at(lines, line))],
        )
        for offset, text in enumerate(lines[line - 1 : end]):
            for token in _TICK.findall(text):
                token = token.strip()
                if not token or len(token) > 160 or "\n" in token:
                    continue
                mention_line = line + offset
                refs.append(
                    RawRef(
                        kind="mention",
                        source=hid,
                        name=token,
                        file=rel,
                        line=mention_line,
                        excerpt=excerpt_at(lines, mention_line),
                    )
                )


def _headings(lines: list[str]) -> list[tuple[int, str, int]]:
    found: list[tuple[int, str, int]] = []
    fence = False
    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        match = _HEADING.match(line)
        if match:
            found.append((len(match.group(1)), match.group(2).strip(), index))
    return found
