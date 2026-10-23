"""Minimal MCP stdio server (JSON-RPC 2.0 over stdio).

Speaks newline-delimited JSON, as the MCP stdio transport specifies, and
also accepts Content-Length framed messages (replying in the same framing).

Tools: graph_query, graph_path, graph_explain, graph_ask.
Logs go to stderr so stdout stays a clean RPC stream.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

from graphloom import __version__
from graphloom.model import Graph, load_graph
from graphloom.query import answer_question, render_explain, render_path, render_query

PROTOCOL = "2024-11-05"

_TOOLS = [
    {
        "name": "graph_query",
        "description": "Find nodes matching a term and return their neighbors inside a token budget. Offline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "Name, id fragment, or file fragment."},
                "budget": {"type": "integer", "description": "Approximate token budget. Default 1600."},
                "depth": {"type": "integer", "description": "Neighbor expansion depth. Default 1."},
            },
            "required": ["term"],
            "additionalProperties": False,
        },
    },
    {
        "name": "graph_path",
        "description": "Shortest path between two nodes, with file:line evidence on each hop. Offline.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["source", "target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "graph_explain",
        "description": "Describe one node: signature, route, and every incoming and outgoing edge. Offline.",
        "inputSchema": {
            "type": "object",
            "properties": {"node": {"type": "string"}},
            "required": ["node"],
            "additionalProperties": False,
        },
    },
    {
        "name": "graph_ask",
        "description": "Retrieve a small subgraph and ask the configured LLM (default AtmoRouter) to answer with citations.",
        "inputSchema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    },
]


def handle_message(
    message: dict,
    graph: Graph,
    client_factory: Callable | None = None,
) -> dict | None:
    """Answer one JSON-RPC message. Notifications return None."""
    method = message.get("method")
    msg_id = message.get("id")
    if method in {"notifications/initialized", "initialized"} or msg_id is None and method:
        if method in {"notifications/initialized", "initialized"}:
            return None
    params = message.get("params") or {}
    if method == "initialize":
        requested = (params.get("protocolVersion") if isinstance(params, dict) else None) or PROTOCOL
        protocol = requested if str(requested).startswith("202") else PROTOCOL
        return _ok(
            msg_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "graphloom", "version": __version__},
            },
        )
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": _TOOLS})
    if method == "tools/call":
        return _call_tool(msg_id, params if isinstance(params, dict) else {}, graph, client_factory)
    if msg_id is None:
        return None
    return _error(msg_id, -32601, f"method not found: {method}")


def serve(graph_path: Path, client_factory: Callable | None = None) -> int:
    graph = load_graph(graph_path)
    print(f"graphloom serve {graph_path}", file=sys.stderr)
    framing = {"mode": "lines"}
    while True:
        message = read_message(sys.stdin.buffer, framing)
        if message is None:
            return 0
        response = handle_message(message, graph, client_factory=client_factory)
        if response is not None:
            write_message(sys.stdout.buffer, response, framed=framing["mode"] == "header")


def read_message(stream, framing: dict | None = None) -> dict | None:
    """Read one message. If ``framing`` is given, record which framing the peer used."""
    if framing is None:
        framing = {}
    prefix = b""
    while True:
        char = stream.read(1)
        if not char:
            return None
        prefix += char
        if prefix.endswith(b"\r\n\r\n") or prefix.endswith(b"\n\n"):
            break
        if len(prefix) > 65536:
            raise ValueError("MCP header too large")
        # A bare JSON line has no header. Detect the end of a single line
        # that started with '{'.
        if prefix.startswith(b"{") and prefix.endswith(b"\n") and b"\n" in prefix:
            framing["mode"] = "lines"
            return json.loads(prefix.decode("utf-8"))
        # Skip blank lines between newline-delimited messages.
        if prefix in (b"\n", b"\r\n"):
            prefix = b""
    if prefix.lstrip().startswith(b"{"):
        framing["mode"] = "lines"
        return json.loads(prefix.decode("utf-8"))
    framing["mode"] = "header"
    length = _content_length(prefix)
    body = stream.read(length)
    if len(body) < length:
        return None
    return json.loads(body.decode("utf-8"))


def write_message(stream, payload: dict, framed: bool = True) -> None:
    """Write one message: Content-Length framed, or one JSON line when framed=False."""
    data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    if framed:
        stream.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii"))
        stream.write(data)
    else:
        stream.write(data + b"\n")
    stream.flush()


def _call_tool(msg_id, params: dict, graph: Graph, client_factory: Callable | None) -> dict:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        return _error(msg_id, -32602, "arguments must be an object")
    try:
        if name == "graph_query":
            text = render_query(
                graph,
                str(arguments.get("term") or ""),
                budget=int(arguments.get("budget") or 1600),
                depth=int(arguments.get("depth") or 1),
            )
            return _tool_result(msg_id, text, False)
        if name == "graph_path":
            text = render_path(graph, str(arguments.get("source") or ""), str(arguments.get("target") or ""))
            missing = text.startswith("no node") or text.startswith("ambiguous") or "hops: none" in text
            return _tool_result(msg_id, text, missing)
        if name == "graph_explain":
            text = render_explain(graph, str(arguments.get("node") or ""))
            missing = text.startswith("no node") or text.startswith("ambiguous")
            return _tool_result(msg_id, text, missing)
        if name == "graph_ask":
            if client_factory is None:
                from graphloom.llm import client_from_env

                client = client_from_env()
            else:
                client = client_factory()
            text, code = answer_question(graph, str(arguments.get("question") or ""), client)
            return _tool_result(msg_id, text, code != 0)
    except Exception as exc:  # noqa: BLE001 — surface tool failures as MCP errors
        return _tool_result(msg_id, f"{type(exc).__name__}: {exc}", True)
    return _error(msg_id, -32602, f"unknown tool: {name}")


def _tool_result(msg_id, text: str, is_error: bool) -> dict:
    return _ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": is_error})


def _ok(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _content_length(header: bytes) -> int:
    for line in header.splitlines():
        if line.lower().startswith(b"content-length:"):
            return int(line.split(b":", 1)[1].strip())
    raise ValueError("missing Content-Length")
