"""Write the human-readable REPORT.md that sits beside graph.json."""

from __future__ import annotations

from collections import defaultdict

from graphloom.model import Graph
from graphloom.query import degree_ignoring_contains, shortest_path

_CODE_RELATIONS = {"imports", "invokes", "touches", "reads", "hits"}


def render_report(graph: Graph) -> str:
    counts = _counts(graph)
    lines: list[str] = [
        "# Graphloom report",
        "",
        f"Root: `{graph.meta.get('root') or '.'}`",
        f"Built: {graph.meta.get('built_at') or ''}",
        "",
        "A map of this repository: code, tables, docs, and config, with an",
        "evidence line on every edge.",
        "",
        "## Counts",
        "",
        f"- Nodes: {counts['nodes']}",
        f"- Edges: {counts['edges']}",
        "",
        "### Nodes by kind",
        "",
    ]
    for kind, count in counts["by_kind"].items():
        lines.append(f"- {kind}: {count}")
    lines.extend(["", "### Nodes by layer", ""])
    for layer, count in counts["by_layer"].items():
        lines.append(f"- {layer}: {count}")
    lines.extend(["", "### Edges by provenance", ""])
    for provenance in ("parsed", "linked", "llm"):
        lines.append(f"- {provenance}: {counts['by_provenance'].get(provenance, 0)}")
    semantic = graph.meta.get("semantic") or {}
    if semantic.get("enabled"):
        lines.extend(
            [
                "",
                "## Semantic pass",
                "",
                f"- Model: `{semantic.get('model') or ''}`",
                f"- Calls: {semantic.get('calls', 0)}",
                f"- Cache hits: {semantic.get('cache_hits', 0)}",
                f"- Concepts: {semantic.get('concepts', 0)}",
            ]
        )
        if semantic.get("error"):
            lines.append(f"- Error: {semantic['error']}")
    lines.extend(["", "## Hubs", ""])
    lines.append("Degree ignores `contains`, so a file is not a hub merely because it defines many symbols.")
    lines.append("")
    lines.append("| Degree | Kind | Name | Node |")
    lines.append("| ---: | --- | --- | --- |")
    degrees = degree_ignoring_contains(graph)
    ranked = sorted(degrees.items(), key=lambda item: (-item[1], item[0]))[:12]
    for node_id, score in ranked:
        node = graph.nodes[node_id]
        lines.append(f"| {score} | {node.kind} | {node.name} | `{node_id}` |")
    if not ranked:
        lines.append("| 0 | | | |")
    lines.extend(["", "## Isolated modules", ""])
    lines.append(
        "Python or script modules whose symbols have no imports, calls, table touches, config reads, or route hits. Documentation mentions do not count."
    )
    lines.append("")
    isolated = _isolated(graph)
    if isolated:
        for file, module_id in isolated:
            lines.append(f"- `{file}` (`{module_id}`)")
    else:
        lines.append("- None.")
    lines.extend(["", "## Cross-layer links", ""])
    grouped: dict[tuple[str, str, str], list] = defaultdict(list)
    for edge in sorted(graph.edges.values(), key=lambda item: item.id):
        if edge.relation == "contains":
            continue
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if source is None or target is None or source.layer == target.layer:
            continue
        grouped[(source.layer, target.layer, edge.relation)].append(edge)
    if not grouped:
        lines.append("- None.")
    for key in sorted(grouped):
        source_layer, target_layer, relation = key
        lines.append(f"### {source_layer} → {target_layer} via {relation}")
        lines.append("")
        for edge in grouped[key][:20]:
            source = graph.nodes[edge.source]
            target = graph.nodes[edge.target]
            evidence = edge.evidence[0] if edge.evidence else None
            where = f" `{evidence.file}:{evidence.line}`" if evidence else ""
            lines.append(
                f"- `{source.name}` {relation} `{target.name}` [{edge.provenance}]{where}"
            )
        lines.append("")
    lines.extend(["## Cross-language links", ""])
    cross = []
    for edge in sorted(graph.edges.values(), key=lambda item: item.id):
        source = graph.nodes.get(edge.source)
        target = graph.nodes.get(edge.target)
        if source is None or target is None:
            continue
        if not source.language or not target.language:
            continue
        if source.language != target.language and edge.relation != "contains":
            cross.append(edge)
    if not cross:
        lines.append("- None.")
    for edge in cross[:20]:
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]
        lines.append(
            f"- `{source.name}` ({source.language}) {edge.relation} `{target.name}` ({target.language}) [{edge.provenance}]"
        )
    lines.extend(["", "## Foreign keys", ""])
    ties = [edge for edge in graph.edges.values() if edge.relation == "ties"]
    if not ties:
        lines.append("- None.")
    for edge in sorted(ties, key=lambda item: item.id):
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]
        lines.append(f"- `{source.name}` → `{target.name}` [{edge.provenance}]")
    lines.extend(["", "## Unread config keys", ""])
    read_ids = {edge.target for edge in graph.edges.values() if edge.relation == "reads"}
    unread = [
        node
        for node in sorted(graph.nodes.values(), key=lambda item: item.id)
        if node.kind == "config" and node.id not in read_ids
    ]
    if unread:
        for node in unread[:30]:
            lines.append(f"- `{node.name}` in `{node.file}:{node.line}`")
    else:
        lines.append("- None.")
    lines.extend(["", "## Suggested questions", ""])
    for question in _questions(graph, ranked):
        lines.append(f"- `{question}`")
    if not _questions(graph, ranked):
        lines.append("- Build a larger project, then ask what a table or route touches.")
    warnings = graph.meta.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def _counts(graph: Graph) -> dict:
    document = graph.to_dict()
    return document["counts"]


def _isolated(graph: Graph) -> list[tuple[str, str]]:
    members: dict[str, set[str]] = defaultdict(set)
    module_of: dict[str, str] = {}
    for node in graph.nodes.values():
        if node.kind == "module":
            members[node.file].add(node.id)
            module_of[node.file] = node.id
        elif node.kind in {"function", "class"} and node.file:
            members[node.file].add(node.id)
    found: list[tuple[str, str]] = []
    for file, ids in sorted(members.items()):
        if file not in module_of:
            continue
        connected = False
        for edge in graph.edges.values():
            if edge.relation not in _CODE_RELATIONS:
                continue
            if edge.source in ids or edge.target in ids:
                connected = True
                break
        if not connected:
            found.append((file, module_of[file]))
    return found


def _questions(graph: Graph, ranked: list[tuple[str, int]]) -> list[str]:
    questions: list[str] = []
    touches: dict[str, int] = defaultdict(int)
    for edge in graph.edges.values():
        if edge.relation == "touches":
            touches[edge.target] += 1
    tables = [
        graph.nodes[node_id]
        for node_id, _count in sorted(touches.items(), key=lambda item: (-item[1], item[0]))
        if node_id in graph.nodes
    ]
    if tables:
        table = tables[0]
        questions.append(f"graphloom query {table.name}")
        questions.append(f'graphloom ask "what touches the {table.name} table?"')
    handlers = [
        node
        for node in graph.nodes.values()
        if node.kind == "function" and node.detail.get("http_path")
    ]
    senders = [
        node
        for node in graph.nodes.values()
        if node.kind == "function" and "send_" in node.name
    ]
    best: tuple[int, str, str] | None = None
    for handler in handlers:
        for sender in senders:
            hops, directed = shortest_path(graph, handler.id, sender.id)
            if not hops or not directed:
                continue
            distance = len(hops) - 1
            if best is None or distance < best[0]:
                best = (distance, handler.name, sender.name)
    if best:
        questions.append(f"graphloom path {best[1]} {best[2]}")
    for node_id, _score in ranked:
        node = graph.nodes[node_id]
        if node.kind in {"function", "class", "table"}:
            questions.append(f"graphloom explain {node.name}")
            break
    deduped: list[str] = []
    seen: set[str] = set()
    for question in questions:
        if question in seen:
            continue
        seen.add(question)
        deduped.append(question)
    return deduped[:6]
