"""Optional semantic pass: send documentation, never source code, to an LLM.

Calls are batched, capped by `--max-calls`, and cached by a hash of the
prompt version, model, symbol catalog, and chunk text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from graphloom.llm import LlmClient
from graphloom.model import Evidence, Graph, Node
from graphloom.util import slugify

PROMPT_VERSION = "graphloom-semantic-v1"
_ANCHOR_KINDS = {"function", "class", "module", "table", "config", "sqlscript"}
_SYSTEM = (
    "You extract concepts from project documentation and ground them in a symbol catalog. "
    "Reply with one JSON object only. Do not wrap it in markdown fences."
)


@dataclass
class Chunk:
    heading_id: str
    file: str
    line: int
    text: str


def run_semantic(
    graph: Graph,
    *,
    root: Path,
    client: LlmClient,
    cache_dir: Path | None,
    max_calls: int,
) -> dict:
    chunks = collect_chunks(root, graph)
    catalog = build_catalog(graph)
    cache_root = cache_dir or (root / ".graphloom" / "cache")
    cache_root.mkdir(parents=True, exist_ok=True)
    hits = 0
    skipped = 0
    concepts = 0
    pending: list[Chunk] = []
    for chunk in chunks:
        key = cache_key(client.model, catalog, chunk.text)
        cached = _read_cache(cache_root, key)
        if cached is None:
            pending.append(chunk)
            continue
        hits += 1
        concepts += _apply(graph, chunk, cached)
    for batch in _batches(pending):
        if client.calls >= max_calls:
            skipped += len(batch)
            continue
        user = _prompt(catalog, batch)
        try:
            raw = client.complete(_SYSTEM, user, max_tokens=900)
            data = parse_model_json(raw)
        except Exception as exc:  # noqa: BLE001 — keep the structural graph
            graph.meta.setdefault("warnings", []).append(f"semantic batch failed: {exc}")
            continue
        grouped: dict[int, list[dict]] = defaultdict(list)
        for concept in data.get("concepts") or []:
            if not isinstance(concept, dict):
                continue
            try:
                index = int(concept.get("chunk", 0))
            except (TypeError, ValueError):
                index = 0
            grouped[index].append(concept)
        for index, chunk in enumerate(batch):
            extracted = grouped.get(index, [])
            _write_cache(cache_root, cache_key(client.model, catalog, chunk.text), extracted)
            concepts += _apply(graph, chunk, extracted)
    return {
        "enabled": True,
        "model": client.model,
        "calls": client.calls,
        "cache_hits": hits,
        "concepts": concepts,
        "chunks": len(chunks),
        "skipped": skipped,
    }


def collect_chunks(root: Path, graph: Graph) -> list[Chunk]:
    headings = [node for node in graph.nodes.values() if node.kind == "heading"]
    by_file: dict[str, list[Node]] = defaultdict(list)
    for heading in headings:
        by_file[heading.file].append(heading)
    chunks: list[Chunk] = []
    files_with_headings = set(by_file)
    for file, heads in sorted(by_file.items()):
        text = _read(root, file)
        if text is None:
            continue
        lines = text.splitlines()
        for heading in sorted(heads, key=lambda node: node.line):
            section = "\n".join(lines[heading.line - 1 : heading.end_line])
            chunks.extend(_pieces(heading.id, heading.file, heading.line, section))
    for node in graph.nodes.values():
        if node.kind != "document" or node.file in files_with_headings:
            continue
        text = _read(root, node.file)
        if text:
            chunks.extend(_pieces(node.id, node.file, 1, text))
    return chunks


def build_catalog(graph: Graph, limit: int = 100) -> str:
    order = {"table": 0, "function": 1, "class": 2, "config": 3, "module": 4, "sqlscript": 5}

    def sort_key(node: Node) -> tuple:
        http = 0 if node.detail.get("http_path") else 1
        return (order.get(node.kind, 9), http, node.name, node.id)

    chosen = [node for node in graph.nodes.values() if node.kind in order]
    chosen.sort(key=sort_key)
    lines = [f"{node.name} | {node.kind} | {node.id}" for node in chosen[:limit]]
    return "\n".join(lines)


def cache_key(model: str, catalog: str, chunk_text: str) -> str:
    digest = hashlib.sha256()
    digest.update(PROMPT_VERSION.encode("utf-8"))
    digest.update(b"\0")
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(catalog.encode("utf-8"))
    digest.update(b"\0")
    digest.update(chunk_text.encode("utf-8"))
    return digest.hexdigest()


def parse_model_json(text: str) -> dict:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model JSON was not an object")
    return data


def _pieces(node_id: str, file: str, line: int, text: str) -> list[Chunk]:
    pieces = _split_text(text.strip(), limit=3500)
    found: list[Chunk] = []
    for piece in pieces:
        if len(piece.strip()) < 40:
            continue
        found.append(Chunk(heading_id=node_id, file=file, line=line, text=piece.strip()))
    return found


def _split_text(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    buffer: list[str] = []
    size = 0
    for paragraph in text.split("\n\n"):
        if size + len(paragraph) > limit and buffer:
            parts.append("\n\n".join(buffer))
            buffer = []
            size = 0
        buffer.append(paragraph)
        size += len(paragraph) + 2
    if buffer:
        parts.append("\n\n".join(buffer))
    return parts


def _prompt(catalog: str, batch: list[Chunk]) -> str:
    documents = []
    for index, chunk in enumerate(batch):
        documents.append(
            f"### chunk {index}\nfile: {chunk.file}:{chunk.line}\n{chunk.text}"
        )
    return (
        "Symbol catalog. Copy anchors exactly from the name or the id column:\n"
        f"{catalog}\n\n"
        'Return {"concepts":[{"chunk":0,"name":"...","summary":"...","anchors":["..."]}]}\n'
        "Use the chunk index from this request. At most 4 concepts per chunk. "
        "Every anchor must be an exact catalog name or id. "
        "If a chunk has no grounded concept, omit it.\n\n"
        "Documents:\n"
        + "\n\n".join(documents)
    )


def _apply(graph: Graph, chunk: Chunk, concepts: list[dict]) -> int:
    created = 0
    for concept in concepts:
        name = str(concept.get("name") or "").strip()
        summary = str(concept.get("summary") or "").strip()
        anchors = concept.get("anchors") or []
        if not name or not isinstance(anchors, list):
            continue
        targets = []
        seen: set[str] = set()
        for anchor in anchors:
            if not isinstance(anchor, str):
                continue
            for node in _resolve_anchor(graph, anchor.strip()):
                if node.id in seen or node.kind not in _ANCHOR_KINDS:
                    continue
                seen.add(node.id)
                targets.append(node)
            if len(targets) >= 6:
                break
        if not targets:
            continue
        slug = slugify(name)
        concept_id = f"concept:{slug}"
        existing = graph.nodes.get(concept_id)
        if existing is None:
            graph.add_node(
                Node(
                    id=concept_id,
                    kind="concept",
                    name=name[:80],
                    layer="concept",
                    language="",
                    file=chunk.file,
                    line=chunk.line,
                    end_line=chunk.line,
                    detail={"summary": summary[:400], "role": "concept"},
                )
            )
            created += 1
        elif summary and not existing.detail.get("summary"):
            existing.detail["summary"] = summary[:400]
        evidence = [
            Evidence(chunk.file, chunk.line, (summary or name)[:160]),
        ]
        if chunk.heading_id in graph.nodes:
            graph.add_edge(concept_id, chunk.heading_id, "about", "llm", evidence)
        for target in targets:
            graph.add_edge(concept_id, target.id, "about", "llm", evidence)
    return created


def _resolve_anchor(graph: Graph, token: str) -> list[Node]:
    if not token:
        return []
    if token in graph.nodes:
        return [graph.nodes[token]]
    exact = [node for node in graph.nodes.values() if node.name == token]
    if len(exact) == 1:
        return exact
    if len(exact) > 1:
        return exact[:6]
    folded = [node for node in graph.nodes.values() if node.name.lower() == token.lower()]
    if len(folded) == 1:
        return folded
    return []


def _batches(chunks: list[Chunk], limit_chars: int = 6000, limit_count: int = 3) -> list[list[Chunk]]:
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    size = 0
    for chunk in chunks:
        if current and (len(current) >= limit_count or size + len(chunk.text) > limit_chars):
            batches.append(current)
            current = []
            size = 0
        current.append(chunk)
        size += len(chunk.text)
    if current:
        batches.append(current)
    return batches


def _read(root: Path, rel: str) -> str | None:
    path = root / rel
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None


def _read_cache(cache_root: Path, key: str) -> list[dict] | None:
    path = cache_root / f"{key}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    concepts = data.get("concepts")
    if not isinstance(concepts, list):
        return None
    return [item for item in concepts if isinstance(item, dict)]


def _write_cache(cache_root: Path, key: str, concepts: list[dict]) -> None:
    path = cache_root / f"{key}.json"
    path.write_text(json.dumps({"concepts": concepts}, indent=2) + "\n", encoding="utf-8")
