"""Offline query, shortest path, explain, and ask-retrieval."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from graphloom.model import Edge, Graph, Node
from graphloom.util import estimate_tokens

_STOP = {
    "the",
    "a",
    "an",
    "what",
    "which",
    "who",
    "how",
    "does",
    "do",
    "is",
    "are",
    "to",
    "of",
    "and",
    "or",
    "in",
    "on",
    "for",
    "from",
    "with",
    "that",
    "this",
    "it",
    "its",
    "be",
    "by",
    "at",
    "as",
    "into",
    "about",
    "where",
    "when",
    "why",
    "can",
    "table",
}


@dataclass
class PathHop:
    node_id: str
    edge: Edge | None
    forward: bool


class LoomIndex:
    def __init__(self, graph: Graph) -> None:
        self.graph = graph
        self.outgoing: dict[str, list[Edge]] = defaultdict(list)
        self.incoming: dict[str, list[Edge]] = defaultdict(list)
        for edge in graph.edges.values():
            self.outgoing[edge.source].append(edge)
            self.incoming[edge.target].append(edge)
        for bucket in (self.outgoing, self.incoming):
            for key in bucket:
                bucket[key].sort(key=lambda edge: (edge.relation, edge.source, edge.target))

    def incident(self, node_id: str) -> list[tuple[str, Edge]]:
        """Return ('out'|'in', edge) pairs for a node."""
        rows = [("out", edge) for edge in self.outgoing.get(node_id, [])]
        rows.extend(("in", edge) for edge in self.incoming.get(node_id, []))
        return rows


def render_query(graph: Graph, term: str, budget: int = 1600, depth: int = 1) -> str:
    index = LoomIndex(graph)
    ranked = _rank(graph, term)
    if not ranked:
        return f"query: {term}\nmatches: 0\nNo nodes matched.\n"
    strong = [item for item in ranked if item[0] >= 50][:8]
    seeds = strong or ranked[:5]
    seed_ids = [node.id for _score, node in seeds]
    kept = _expand(index, seed_ids, depth, limit=30)
    blocks: list[str] = []
    used = 0
    shown = 0
    truncated = False
    for node_id in kept:
        block = _node_block(graph, index, node_id, focus=set(kept))
        cost = estimate_tokens(block)
        if blocks and used + cost > budget:
            truncated = True
            break
        blocks.append(block)
        used += cost
        shown += 1
        if used > budget:
            truncated = True
            break
    header = [
        f"query: {term}",
        f"matches: {len(ranked)}",
        f"showing: {shown} nodes, depth {depth}",
        f"budget: {budget} tokens (~{used} used)",
    ]
    if truncated:
        header.append("truncated: yes")
    return "\n".join(header) + "\n\n" + "\n".join(blocks)


def render_explain(graph: Graph, term: str) -> str:
    node, error = resolve_one(graph, term)
    if error or node is None:
        return error or f"no node matches {term!r}\n"
    index = LoomIndex(graph)
    lines = [
        f"explain: {node.id}",
        f"kind: {node.kind}",
        f"name: {node.name}",
        f"layer: {node.layer}",
        f"at: {node.file}:{node.line}-{node.end_line}",
    ]
    signature = node.detail.get("signature")
    if signature:
        lines.append(f"signature: {signature}")
    if node.detail.get("http_path"):
        method = node.detail.get("http_method") or ""
        route = f"{method} {node.detail['http_path']}".strip()
        lines.append(f"route: {route}")
    for key in ("qualname", "http_method", "http_path", "columns", "summary"):
        value = node.detail.get(key)
        if value:
            if key == "columns" and isinstance(value, list):
                lines.append(f"columns: {', '.join(str(item) for item in value)}")
            else:
                lines.append(f"{key}: {value}")
    outgoing = index.outgoing.get(node.id, [])
    incoming = index.incoming.get(node.id, [])
    lines.append("")
    lines.append(f"outgoing: {len(outgoing)}")
    for edge in outgoing:
        lines.extend(_edge_lines(graph, edge, "->"))
    lines.append("")
    lines.append(f"incoming: {len(incoming)}")
    for edge in incoming:
        lines.extend(_edge_lines(graph, edge, "<-"))
    return "\n".join(lines) + "\n"


def render_path(graph: Graph, source_term: str, target_term: str) -> str:
    source, source_error = resolve_one(graph, source_term)
    target, target_error = resolve_one(graph, target_term)
    if source is None or target is None:
        return (source_error or target_error or "path endpoints did not resolve") + "\n"
    hops, directed = shortest_path(graph, source.id, target.id)
    if hops is None:
        return (
            f"path: {source.name} -> {target.name}\n"
            f"resolved: {source.id} -> {target.id}\n"
            "hops: none\n"
            "No path connects these nodes.\n"
        )
    lines = [
        f"path: {source.name} -> {target.name}",
        f"resolved: {source.id} -> {target.id}",
        f"hops: {max(0, len(hops) - 1)}",
        f"directed: {'yes' if directed else 'no'}",
        "",
    ]
    for index, hop in enumerate(hops, start=1):
        node = graph.nodes[hop.node_id]
        lines.append(f"{index}. {node.id}")
        lines.append(f"   {node.kind} {node.name}")
        lines.append(f"   {node.file}:{node.line}")
        if hop.edge is not None:
            arrow = "->" if hop.forward else "<-"
            evidence = hop.edge.evidence[0] if hop.edge.evidence else None
            where = ""
            if evidence:
                excerpt = f"  {evidence.excerpt}" if evidence.excerpt else ""
                where = f"   {evidence.file}:{evidence.line}{excerpt}"
            lines.append(
                f"   {hop.edge.relation} [{hop.edge.provenance}] {arrow} "
                f"{hop.edge.target if hop.forward else hop.edge.source}"
            )
            if where:
                lines.append(where)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def answer_question(graph: Graph, question: str, client: object) -> tuple[str, int]:
    """Ask an LLM about a retrieved subgraph. Returns text and an exit code."""
    from graphloom.llm import LlmError

    nodes, edges = retrieve(graph, question)
    if not nodes:
        return (
            f"ask: {question}\n"
            "matches: 0\n"
            "Nothing in the graph matched the question, so no model call was made.\n",
            1,
        )
    rendered = render_subgraph(graph, nodes, edges)
    system = (
        "You answer questions about a repository using only the subgraph. "
        "Cite node ids and file:line locations from the subgraph. "
        "If the subgraph does not contain the answer, say what is missing. "
        "Be concise."
    )
    user = f"Question: {question}\n\nSubgraph:\n{rendered}"
    try:
        answer = client.complete(system, user, max_tokens=700)  # type: ignore[attr-defined]
    except LlmError as exc:
        return f"{rendered}\n\nask failed: {exc}\n", 2
    model = getattr(client, "model", "")
    text = (
        f"ask: {question}\n"
        f"model: {model}\n"
        f"retrieved: {len(nodes)} nodes, {len(edges)} edges\n\n"
        f"{answer.strip()}\n\n"
        "--- subgraph used ---\n"
        f"{rendered}"
    )
    return text, 0


def retrieve(graph: Graph, question: str, limit_nodes: int = 24, limit_edges: int = 36):
    tokens = [
        token
        for token in re.split(r"[^A-Za-z0-9_]+", question.lower())
        if len(token) >= 3 and token not in _STOP
    ]
    if not tokens:
        return [], []
    scored: list[tuple[int, Node]] = []
    for node in graph.nodes.values():
        score = 0
        hay_name = node.name.lower()
        hay_id = node.id.lower()
        hay_file = (node.file or "").lower()
        qual = str(node.detail.get("qualname") or "").lower()
        for token in tokens:
            if hay_name == token:
                score += 10
            elif token in hay_name or token in qual:
                score += 5
            elif token in hay_id:
                score += 3
            elif token in hay_file:
                score += 2
        if score:
            scored.append((score, node))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    seeds = [node.id for _score, node in scored[:8]]
    if not seeds:
        return [], []
    index = LoomIndex(graph)
    kept_ids = _expand(index, seeds, depth=1, limit=limit_nodes)
    kept = set(kept_ids)
    edges: list[Edge] = []
    for edge in sorted(graph.edges.values(), key=lambda item: item.id):
        if edge.source in kept and edge.target in kept:
            edges.append(edge)
        if len(edges) >= limit_edges:
            break
    nodes = [graph.nodes[node_id] for node_id in kept_ids if node_id in graph.nodes]
    return nodes, edges


def render_subgraph(graph: Graph, nodes: list[Node], edges: list[Edge]) -> str:
    lines = ["nodes:"]
    for node in nodes:
        detail = _detail_phrase(node)
        extra = f"  {detail}" if detail else ""
        lines.append(f"- {node.id}  {node.kind} {node.name}  {node.file}:{node.line}{extra}")
    lines.append("edges:")
    if not edges:
        lines.append("- (none)")
    for edge in edges:
        evidence = edge.evidence[0] if edge.evidence else None
        where = f"  {evidence.file}:{evidence.line} {evidence.excerpt}".rstrip() if evidence else ""
        lines.append(
            f"- {edge.source} -{edge.relation} [{edge.provenance}]-> {edge.target}{where}"
        )
    return "\n".join(lines) + "\n"


def resolve_one(graph: Graph, term: str) -> tuple[Node | None, str | None]:
    term = term.strip()
    if not term:
        return None, "empty node term\n"
    nodes = list(graph.nodes.values())
    exact_id = [node for node in nodes if node.id == term]
    if len(exact_id) == 1:
        return exact_id[0], None
    exact_name = [node for node in nodes if node.name == term]
    if len(exact_name) == 1:
        return exact_name[0], None
    if len(exact_name) > 1:
        return None, _ambiguous(term, exact_name)
    folded = [
        node
        for node in nodes
        if node.name.lower() == term.lower() or node.id.lower() == term.lower()
    ]
    if len(folded) == 1:
        return folded[0], None
    if len(folded) > 1:
        return None, _ambiguous(term, folded)
    partial = [
        node
        for node in nodes
        if term.lower() in node.name.lower() or term.lower() in node.id.lower()
    ]
    if len(partial) == 1:
        return partial[0], None
    if not partial:
        return None, f"no node matches {term!r}\n"
    return None, _ambiguous(term, partial)


def shortest_path(graph: Graph, source: str, target: str) -> tuple[list[PathHop] | None, bool]:
    directed = _bfs(graph, source, target, directed=True)
    if directed is not None:
        return directed, True
    return _bfs(graph, source, target, directed=False), False


def degree_ignoring_contains(graph: Graph) -> dict[str, int]:
    scores: dict[str, int] = defaultdict(int)
    for edge in graph.edges.values():
        if edge.relation == "contains":
            continue
        scores[edge.source] += 1
        scores[edge.target] += 1
    return scores


def _bfs(graph: Graph, source: str, target: str, directed: bool) -> list[PathHop] | None:
    if source == target:
        return [PathHop(source, None, True)]
    adjacent: dict[str, list[tuple[Edge, str, bool]]] = defaultdict(list)
    for edge in graph.edges.values():
        adjacent[edge.source].append((edge, edge.target, True))
        if not directed:
            adjacent[edge.target].append((edge, edge.source, False))
    queue = [source]
    parent: dict[str, tuple[str, Edge, bool]] = {}
    seen = {source}
    while queue:
        current = queue.pop(0)
        for edge, neighbor, forward in adjacent.get(current, []):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            parent[neighbor] = (current, edge, forward)
            if neighbor == target:
                return _reconstruct(target, parent)
            queue.append(neighbor)
    return None


def _reconstruct(target: str, parent: dict[str, tuple[str, Edge, bool]]) -> list[PathHop]:
    hops: list[PathHop] = [PathHop(target, None, True)]
    cursor = target
    while cursor in parent:
        previous, edge, forward = parent[cursor]
        hops[-1] = PathHop(cursor, edge, forward)
        hops.append(PathHop(previous, None, True))
        cursor = previous
    hops.reverse()
    # The edge stored on a hop describes how we arrived, so move it onto the
    # previous hop (the step taken from that node).
    if len(hops) >= 2:
        for index in range(len(hops) - 1):
            hops[index] = PathHop(hops[index].node_id, hops[index + 1].edge, hops[index + 1].forward)
        hops[-1] = PathHop(hops[-1].node_id, None, True)
    return hops


def _rank(graph: Graph, term: str) -> list[tuple[int, Node]]:
    ranked = []
    for node in graph.nodes.values():
        score = _score(node, term)
        if score:
            ranked.append((score, node))
    ranked.sort(key=lambda item: (-item[0], item[1].id))
    return ranked


def _score(node: Node, term: str) -> int:
    needle = term.lower().strip()
    if not needle:
        return 0
    if node.id.lower() == needle:
        return 100
    if node.name.lower() == needle:
        return 90
    qual = str(node.detail.get("qualname") or "").lower()
    if qual == needle:
        return 88
    if node.name.lower().startswith(needle):
        return 70
    if needle in node.name.lower() or needle in qual:
        return 60
    if needle in node.id.lower():
        return 40
    if needle in (node.file or "").lower():
        return 30
    for value in node.detail.values():
        if isinstance(value, str) and needle in value.lower():
            return 20
        if isinstance(value, list) and any(needle in str(item).lower() for item in value):
            return 20
    return 0


def _expand(index: LoomIndex, seeds: list[str], depth: int, limit: int) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    frontier = []
    for seed in seeds:
        if seed in index.graph.nodes and seed not in seen:
            seen.add(seed)
            kept.append(seed)
            frontier.append(seed)
    for _ in range(max(0, depth)):
        nxt: list[str] = []
        for node_id in frontier:
            for _direction, edge in index.incident(node_id):
                other = edge.target if edge.source == node_id else edge.source
                if other in seen or other not in index.graph.nodes:
                    continue
                seen.add(other)
                kept.append(other)
                nxt.append(other)
                if len(kept) >= limit:
                    return kept
        frontier = nxt
    return kept[:limit]


def _node_block(graph: Graph, index: LoomIndex, node_id: str, focus: set[str]) -> str:
    node = graph.nodes[node_id]
    lines = [
        f"## {node.id}",
        f"kind: {node.kind}   layer: {node.layer}   name: {node.name}",
        f"at: {node.file}:{node.line}",
    ]
    detail = _detail_phrase(node)
    if detail:
        lines.append(detail)
    lines.append("edges:")
    rows = []
    for direction, edge in index.incident(node_id):
        other = edge.target if direction == "out" else edge.source
        if focus and other not in focus and edge.relation == "contains":
            continue
        arrow = "->" if direction == "out" else "<-"
        evidence = edge.evidence[0] if edge.evidence else None
        where = f"  {evidence.file}:{evidence.line}" if evidence else ""
        excerpt = f"  {evidence.excerpt}" if evidence and evidence.excerpt else ""
        other_name = graph.nodes[other].name if other in graph.nodes else other
        rows.append(
            f"  {arrow} {edge.relation} [{edge.provenance}] {other} ({other_name}){where}{excerpt}"
        )
    lines.extend(rows or ["  (none)"])
    return "\n".join(lines) + "\n"


def _edge_lines(graph: Graph, edge: Edge, arrow: str) -> list[str]:
    other_id = edge.target if arrow == "->" else edge.source
    other = graph.nodes.get(other_id)
    label = other.name if other else other_id
    lines = [f"  {arrow} {edge.relation} [{edge.provenance}] {other_id} ({label})"]
    for evidence in edge.evidence[:3]:
        excerpt = f"  {evidence.excerpt}" if evidence.excerpt else ""
        lines.append(f"     {evidence.file}:{evidence.line}{excerpt}")
    return lines


def _detail_phrase(node: Node) -> str:
    parts: list[str] = []
    signature = node.detail.get("signature")
    if isinstance(signature, str) and signature:
        parts.append(f"signature: {signature}")
    columns = node.detail.get("columns")
    if isinstance(columns, list) and columns:
        parts.append("columns: " + ", ".join(str(item) for item in columns))
    http_path = node.detail.get("http_path")
    if http_path:
        method = node.detail.get("http_method") or ""
        parts.append(f"route: {method} {http_path}".strip())
    summary = node.detail.get("summary")
    if isinstance(summary, str) and summary:
        parts.append(f"summary: {summary}")
    return "\n".join(parts)


def _ambiguous(term: str, nodes: list[Node]) -> str:
    lines = [f"ambiguous: {term!r} matches {len(nodes)} nodes"]
    for node in sorted(nodes, key=lambda item: item.id)[:8]:
        lines.append(f"  {node.id}  {node.kind} {node.name}")
    if len(nodes) > 8:
        lines.append(f"  … {len(nodes) - 8} more")
    return "\n".join(lines) + "\n"
