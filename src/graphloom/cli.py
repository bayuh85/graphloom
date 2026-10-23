"""Command line interface for Graphloom."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from graphloom import __version__
from graphloom.build import build_repository, write_outputs
from graphloom.llm import LlmError, client_from_env
from graphloom.mcp_server import serve
from graphloom.model import Graph, load_graph
from graphloom.query import answer_question, render_explain, render_path, render_query


class GraphNotFound(FileNotFoundError):
    """No graph.json was found from the working directory upward."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="graphloom",
        description="Weave a repository into a queryable knowledge graph.",
    )
    parser.add_argument("--version", action="version", version=f"graphloom {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Walk a repository and write .graphloom/")
    build.add_argument("path", nargs="?", default=".", help="Repository root (default: .)")
    build.add_argument("--semantic", action="store_true", help="Extract concepts from docs via the LLM")
    build.add_argument("--max-calls", type=int, default=8, help="Cap semantic model calls (default: 8)")
    build.add_argument("--out", default=None, help="Output directory (default: <path>/.graphloom)")

    query = sub.add_parser("query", help="Match a term and show neighbors")
    query.add_argument("term")
    query.add_argument("--budget", type=int, default=1600, help="Approximate token budget")
    query.add_argument("--depth", type=int, default=1)
    _add_graph_arg(query)

    path = sub.add_parser("path", help="Shortest path between two nodes")
    path.add_argument("source")
    path.add_argument("target")
    _add_graph_arg(path)

    explain = sub.add_parser("explain", help="Show one node and its edges")
    explain.add_argument("node")
    _add_graph_arg(explain)

    ask = sub.add_parser("ask", help="Answer a question from a retrieved subgraph")
    ask.add_argument("question")
    _add_graph_arg(ask)

    server = sub.add_parser("serve", help="MCP stdio server for coding agents")
    _add_graph_arg(server)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            return _build(args)
        graph = _load(args.graph)
        if args.command == "query":
            return _query(graph, args)
        if args.command == "path":
            return _path(graph, args)
        if args.command == "explain":
            return _explain(graph, args)
        if args.command == "ask":
            return _ask(graph, args)
        if args.command == "serve":
            return serve(Path(args.graph) if args.graph else find_graph_file(None))
    except GraphNotFound as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, LlmError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 2


def _add_graph_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--graph",
        default=None,
        help="Path to graph.json (default: search upward for .graphloom/graph.json)",
    )


def _build(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser().resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 1
    if args.max_calls < 0:
        print("--max-calls must be >= 0", file=sys.stderr)
        return 1
    out = Path(args.out).expanduser().resolve() if args.out else root / ".graphloom"
    client = client_from_env() if args.semantic else None
    graph = build_repository(
        root,
        semantic=args.semantic,
        max_calls=args.max_calls,
        client=client,
        cache_dir=out / "cache",
    )
    written = write_outputs(graph, out)
    counts = graph.to_dict()["counts"]
    provenance = counts["by_provenance"]
    print(f"graphloom build {_display(root)}")
    print(f"nodes: {counts['nodes']}")
    print(f"edges: {counts['edges']}")
    print(f"  parsed: {provenance.get('parsed', 0)}")
    print(f"  linked: {provenance.get('linked', 0)}")
    print(f"  llm: {provenance.get('llm', 0)}")
    semantic = graph.meta.get("semantic") or {}
    if semantic.get("enabled"):
        print(
            "semantic: "
            f"calls={semantic.get('calls', 0)} "
            f"cache_hits={semantic.get('cache_hits', 0)} "
            f"concepts={semantic.get('concepts', 0)} "
            f"model={semantic.get('model', '')}"
        )
    for label, path in written.items():
        print(f"wrote {label}: {_display(path)}")
    for warning in graph.meta.get("warnings") or []:
        print(f"warning: {warning}", file=sys.stderr)
    return 0


def _query(graph: Graph, args: argparse.Namespace) -> int:
    text = render_query(graph, args.term, budget=args.budget, depth=args.depth)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    return 1 if "matches: 0" in text else 0


def _path(graph: Graph, args: argparse.Namespace) -> int:
    text = render_path(graph, args.source, args.target)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    failed = text.startswith("no node") or text.startswith("ambiguous") or "hops: none" in text
    return 1 if failed else 0


def _explain(graph: Graph, args: argparse.Namespace) -> int:
    text = render_explain(graph, args.node)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    failed = text.startswith("no node") or text.startswith("ambiguous") or text.startswith("empty")
    return 1 if failed else 0


def _ask(graph: Graph, args: argparse.Namespace) -> int:
    try:
        client = client_from_env()
    except LlmError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text, code = answer_question(graph, args.question, client)
    sys.stdout.write(text if text.endswith("\n") else text + "\n")
    if code == 2:
        print(text.strip().splitlines()[-1], file=sys.stderr)
    return code


def find_graph_file(explicit: str | None) -> Path:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise GraphNotFound(f"graph not found: {path}")
        return path.resolve()
    current = Path.cwd()
    for directory in [current, *current.parents]:
        candidate = directory / ".graphloom" / "graph.json"
        if candidate.is_file():
            return candidate.resolve()
    raise GraphNotFound("no .graphloom/graph.json found; run graphloom build first")


def _load(explicit: str | None) -> Graph:
    return load_graph(find_graph_file(explicit))


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
