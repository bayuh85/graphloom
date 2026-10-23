"""Weave a repository into a Graphloom graph."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from graphloom.extract_config import extract_config
from graphloom.extract_jsts import extract_jsts
from graphloom.extract_markdown import extract_markdown
from graphloom.extract_python import extract_python
from graphloom.extract_sql import extract_sql
from graphloom.link import link_all
from graphloom.model import Binding, Graph, RawRef, dump_graph
from graphloom.scan import (
    CONFIG_SUFFIXES,
    JS_SUFFIXES,
    MD_SUFFIXES,
    PY_SUFFIXES,
    SQL_SUFFIXES,
    iter_source_files,
    read_text,
    rel_posix,
)


def build_repository(
    root: Path,
    *,
    semantic: bool = False,
    max_calls: int = 8,
    client: object | None = None,
    cache_dir: Path | None = None,
    now: datetime | None = None,
) -> Graph:
    """Walk `root` offline and, when asked, ground documentation with an LLM."""
    root = root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")
    stamp = now or datetime.now(timezone.utc).replace(microsecond=0)
    graph = Graph()
    graph.meta = {
        "root": str(root),
        "built_at": stamp.isoformat(),
        "warnings": [],
        "semantic": {"enabled": False, "calls": 0, "cache_hits": 0},
    }
    refs: list[RawRef] = []
    bindings: list[Binding] = []
    for path in iter_source_files(root):
        rel = rel_posix(root, path)
        source = read_text(path)
        if source is None:
            graph.meta["warnings"].append(f"{rel}: skipped (unreadable or minified)")
            continue
        suffix = path.suffix.lower()
        try:
            if suffix in PY_SUFFIXES:
                extract_python(graph, refs, bindings, rel, source)
            elif suffix in JS_SUFFIXES:
                extract_jsts(graph, refs, rel, source)
            elif suffix in SQL_SUFFIXES:
                extract_sql(graph, refs, rel, source)
            elif suffix in MD_SUFFIXES:
                extract_markdown(graph, refs, rel, source)
            elif suffix in CONFIG_SUFFIXES:
                extract_config(graph, rel, source)
        except Exception as exc:  # one bad file should not drop the rest
            graph.meta["warnings"].append(f"{rel}: {type(exc).__name__}: {exc}")
    link_all(graph, refs, bindings)
    if semantic:
        _run_semantic(graph, root, max_calls=max_calls, client=client, cache_dir=cache_dir)
    return graph


def _run_semantic(
    graph: Graph,
    root: Path,
    *,
    max_calls: int,
    client: object | None,
    cache_dir: Path | None,
) -> None:
    from graphloom.llm import LlmError, client_from_env
    from graphloom.semantic import run_semantic

    if client is None:
        client = client_from_env()
    try:
        stats = run_semantic(
            graph,
            root=root,
            client=client,  # type: ignore[arg-type]
            cache_dir=cache_dir,
            max_calls=max_calls,
        )
    except LlmError as exc:
        graph.meta["warnings"].append(str(exc))
        stats = {
            "enabled": True,
            "error": str(exc),
            "model": getattr(client, "model", ""),
            "calls": getattr(client, "calls", 0),
            "cache_hits": 0,
        }
    graph.meta["semantic"] = stats


def write_outputs(graph: Graph, out_dir: Path) -> dict[str, Path]:
    """Write graph.json, REPORT.md, and the offline graph.html viewer."""
    from graphloom.report import render_report
    from graphloom.viewer import render_html

    out_dir.mkdir(parents=True, exist_ok=True)
    graph_path = out_dir / "graph.json"
    report_path = out_dir / "REPORT.md"
    html_path = out_dir / "graph.html"
    dump_graph(graph, graph_path)
    report_path.write_text(render_report(graph), encoding="utf-8")
    html_path.write_text(render_html(graph), encoding="utf-8")
    return {"graph": graph_path, "report": report_path, "html": html_path}
