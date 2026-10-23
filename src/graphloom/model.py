"""In-memory graph and the on-disk `graphloom.graph/v1` document."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from graphloom import __version__
from graphloom.util import PROVENANCE_RANK, SCHEMA


@dataclass
class Evidence:
    file: str
    line: int
    excerpt: str

    def to_dict(self) -> dict:
        return {"file": self.file, "line": self.line, "excerpt": self.excerpt}

    @classmethod
    def from_dict(cls, data: dict) -> Evidence:
        return cls(
            file=str(data.get("file") or ""),
            line=int(data.get("line") or 1),
            excerpt=str(data.get("excerpt") or ""),
        )


@dataclass
class Node:
    id: str
    kind: str
    name: str
    layer: str
    language: str
    file: str
    line: int
    end_line: int
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "layer": self.layer,
            "language": self.language,
            "file": self.file,
            "line": self.line,
            "end_line": self.end_line,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Node:
        return cls(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            layer=data.get("layer") or "",
            language=data.get("language") or "",
            file=data.get("file") or "",
            line=int(data.get("line") or 1),
            end_line=int(data.get("end_line") or data.get("line") or 1),
            detail=dict(data.get("detail") or {}),
        )


@dataclass
class Edge:
    id: str
    source: str
    target: str
    relation: str
    provenance: str
    evidence: list[Evidence] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "target": self.target,
            "relation": self.relation,
            "provenance": self.provenance,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Edge:
        return cls(
            id=data["id"],
            source=data["source"],
            target=data["target"],
            relation=data["relation"],
            provenance=data["provenance"],
            evidence=[Evidence.from_dict(item) for item in data.get("evidence") or []],
        )


@dataclass
class RawRef:
    """A fact an extractor saw but has not resolved to a node yet."""

    kind: str
    source: str
    name: str
    file: str
    line: int
    excerpt: str
    receiver: str | None = None
    module: str | None = None
    note: str | None = None
    scopes: tuple[str, ...] = ()


@dataclass
class Binding:
    """A name introduced by an import, later resolved to a node id."""

    scope_id: str
    module_id: str
    local: str
    imported: str | None
    from_module: str | None
    file: str
    line: int
    excerpt: str
    resolved_id: str | None = None


class Graph:
    """A directed multigraph keyed by stable node and edge ids."""

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self.meta: dict = {}

    def add_node(self, node: Node) -> Node:
        current = self.nodes.get(node.id)
        if current is None:
            self.nodes[node.id] = node
            return node
        # A foreign-key stub yields to the real CREATE TABLE.
        if current.detail.get("stub") and not node.detail.get("stub"):
            self.nodes[node.id] = node
            return node
        return current

    def add_edge(
        self,
        source: str,
        target: str,
        relation: str,
        provenance: str,
        evidence: list[Evidence],
    ) -> Edge | None:
        if source == target:
            return None
        if source not in self.nodes or target not in self.nodes:
            return None
        if provenance not in PROVENANCE_RANK:
            raise ValueError(f"unknown provenance: {provenance}")
        edge_id = f"e:{relation}:{source}->{target}"
        existing = self.edges.get(edge_id)
        if existing is None:
            edge = Edge(
                id=edge_id,
                source=source,
                target=target,
                relation=relation,
                provenance=provenance,
                evidence=_dedupe_evidence(evidence)[:8],
            )
            self.edges[edge_id] = edge
            return edge
        if PROVENANCE_RANK[provenance] > PROVENANCE_RANK[existing.provenance]:
            existing.provenance = provenance
        existing.evidence = _dedupe_evidence([*existing.evidence, *evidence])[:8]
        return existing

    def to_dict(self) -> dict:
        nodes = [node.to_dict() for node in sorted(self.nodes.values(), key=lambda n: n.id)]
        edges = [edge.to_dict() for edge in sorted(self.edges.values(), key=lambda e: e.id)]
        by_kind: dict[str, int] = {}
        by_layer: dict[str, int] = {}
        for node in self.nodes.values():
            by_kind[node.kind] = by_kind.get(node.kind, 0) + 1
            by_layer[node.layer] = by_layer.get(node.layer, 0) + 1
        by_prov: dict[str, int] = {}
        for edge in self.edges.values():
            by_prov[edge.provenance] = by_prov.get(edge.provenance, 0) + 1
        semantic = self.meta.get("semantic") or {"enabled": False, "calls": 0, "cache_hits": 0}
        return {
            "schema": SCHEMA,
            "tool": {"name": "graphloom", "version": __version__},
            "built_at": self.meta.get("built_at"),
            "root": self.meta.get("root"),
            "semantic": semantic,
            "warnings": list(self.meta.get("warnings") or []),
            "counts": {
                "nodes": len(self.nodes),
                "edges": len(self.edges),
                "by_kind": dict(sorted(by_kind.items())),
                "by_layer": dict(sorted(by_layer.items())),
                "by_provenance": dict(sorted(by_prov.items())),
            },
            "nodes": nodes,
            "edges": edges,
        }


def dump_graph(graph: Graph, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph.to_dict(), indent=2) + "\n", encoding="utf-8")


def load_graph(path: Path) -> Graph:
    data = json.loads(path.read_text(encoding="utf-8"))
    schema = data.get("schema")
    if schema != SCHEMA:
        raise ValueError(f"unsupported graph schema {schema!r}; expected {SCHEMA}")
    graph = Graph()
    graph.meta = {
        "built_at": data.get("built_at"),
        "root": data.get("root"),
        "semantic": data.get("semantic") or {},
        "warnings": list(data.get("warnings") or []),
        "tool": data.get("tool") or {},
    }
    for raw in data.get("nodes") or []:
        node = Node.from_dict(raw)
        graph.nodes[node.id] = node
    for raw in data.get("edges") or []:
        edge = Edge.from_dict(raw)
        graph.edges[edge.id] = edge
    return graph


def _dedupe_evidence(items: list[Evidence]) -> list[Evidence]:
    seen: set[tuple[str, int, str]] = set()
    kept: list[Evidence] = []
    for item in items:
        key = (item.file, item.line, item.excerpt)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept
