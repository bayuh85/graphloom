"""Query helpers, the ask retrieval, and the MCP server."""

import os
import subprocess
import sys

from graphloom.llm import LlmClient, LlmError
from graphloom.mcp_server import handle_message, read_message, write_message
from graphloom.model import Evidence, Graph, Node
from graphloom.query import answer_question, render_path, resolve_one


class FakeClient:
    def __init__(self, text='{"concepts": []}', fail=False):
        self.text = text
        self.fail = fail
        self.calls = 0
        self.model = "fake-model"
        self.seen = ""

    def complete(self, system, user, max_tokens=700):
        self.calls += 1
        self.seen = user
        if self.fail:
            raise LlmError("boom")
        return self.text


def test_ambiguous_name_is_rejected():
    graph = Graph()
    for file in ("a.py", "b.py"):
        graph.add_node(
            Node(
                id=f"fn:{file}:run",
                kind="function",
                name="run",
                layer="code",
                language="python",
                file=file,
                line=1,
                end_line=2,
            )
        )
    node, error = resolve_one(graph, "run")
    assert node is None
    assert error and "ambiguous" in error


def test_directed_path_beats_a_shorter_backwards_edge():
    graph = Graph()
    ids = []
    for name in ("a", "b", "c"):
        node_id = f"fn:{name}.py:{name}"
        ids.append(node_id)
        graph.add_node(
            Node(
                id=node_id,
                kind="function",
                name=name,
                layer="code",
                language="python",
                file=f"{name}.py",
                line=1,
                end_line=1,
            )
        )
    graph.add_edge(ids[0], ids[1], "invokes", "parsed", [Evidence("a.py", 2, "b()")])
    graph.add_edge(ids[1], ids[2], "invokes", "parsed", [Evidence("b.py", 3, "c()")])
    graph.add_edge(ids[2], ids[0], "invokes", "linked", [Evidence("c.py", 4, "a()")])
    text = render_path(graph, "a", "c")
    assert "directed: yes" in text
    assert "hops: 2" in text
    steps = text.split("directed: yes", 1)[1]
    assert steps.index("fn:a.py:a") < steps.index("fn:b.py:b") < steps.index("fn:c.py:c")


def test_ask_cites_subgraph_and_skips_the_model_when_nothing_matches():
    graph = _mini()
    client = FakeClient("create_invoice writes invoices at sql/schema.sql:1.")
    text, code = answer_question(graph, "what touches the invoices table?", client)
    assert code == 0
    assert client.calls == 1
    assert "tbl:invoices" in client.seen
    assert "create_invoice writes invoices" in text
    assert "subgraph used" in text
    quiet = FakeClient("should not be called")
    text, code = answer_question(graph, "zzzzqqq", quiet)
    assert code == 1
    assert quiet.calls == 0
    assert "no model call" in text


def test_ask_failure_returns_exit_code_2():
    graph = _mini()
    client = LlmClient("https://atmorouter.dev/v1", "", "zai/glm-5.3-flash")
    text, code = answer_question(graph, "what touches the invoices table?", client)
    assert code == 2
    assert "API key" in text
    assert "tbl:invoices" in text


def test_mcp_tools_round_trip_without_a_model():
    graph = _mini()
    init = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        graph,
    )
    assert init["result"]["serverInfo"]["name"] == "graphloom"
    assert handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}, graph) is None
    listed = handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, graph)
    names = [tool["name"] for tool in listed["result"]["tools"]]
    assert names == ["graph_query", "graph_path", "graph_explain", "graph_ask"]
    queried = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "graph_query", "arguments": {"term": "invoices"}},
        },
        graph,
    )
    assert queried["result"]["isError"] is False
    assert "tbl:invoices" in queried["result"]["content"][0]["text"]
    asked = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "graph_ask", "arguments": {"question": "invoices"}},
        },
        graph,
        client_factory=lambda: FakeClient("cited fn:app.py:create_invoice app.py:1"),
    )
    assert "cited fn:app.py:create_invoice" in asked["result"]["content"][0]["text"]
    unknown = handle_message({"jsonrpc": "2.0", "id": 5, "method": "nope"}, graph)
    assert unknown["error"]["code"] == -32601


def test_mcp_stdio_framing(tmp_path):
    from graphloom.build import build_repository, write_outputs
    from paths import EXAMPLE

    graph = build_repository(EXAMPLE)
    out = tmp_path / "graph.json"
    write_outputs(graph, tmp_path)
    env = os.environ.copy()
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "graphloom", "serve", "--graph", str(out)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdin and proc.stdout
    write_message(
        proc.stdin,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {}},
        },
    )
    write_message(proc.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    write_message(
        proc.stdin,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "graph_path", "arguments": {"source": "post_invoice", "target": "send_receipt"}},
        },
    )
    proc.stdin.close()
    first = read_message(proc.stdout)
    second = read_message(proc.stdout)
    proc.wait(timeout=20)
    assert first is not None and first["id"] == 1
    assert first["result"]["protocolVersion"] == "2025-03-26"
    assert second is not None and second["id"] == 2
    body = second["result"]["content"][0]["text"]
    assert "send_receipt" in body
    assert "hops: 4" in body
    assert proc.returncode == 0



def test_serve_speaks_newline_delimited_json(tmp_path):
    """Claude Code and Cursor send one JSON object per line and expect the same back."""
    import json as _json

    from graphloom.build import build_repository, write_outputs
    from paths import EXAMPLE

    graph = build_repository(EXAMPLE)
    out = tmp_path / "graph.json"
    write_outputs(graph, tmp_path)
    env = os.environ.copy()
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = "".join(_json.dumps(m) + "\n" for m in lines).encode()
    proc = subprocess.run(
        [sys.executable, "-m", "graphloom", "serve", "--graph", str(out)],
        input=stdin,
        capture_output=True,
        env=env,
        timeout=20,
    )
    assert proc.returncode == 0
    replies = [_json.loads(x) for x in proc.stdout.decode().splitlines() if x.strip()]
    assert [r["id"] for r in replies] == [1, 2]
    assert {t["name"] for t in replies[1]["result"]["tools"]} >= {"graph_query", "graph_path"}


def _mini() -> Graph:
    graph = Graph()
    graph.add_node(
        Node(
            id="fn:app.py:create_invoice",
            kind="function",
            name="create_invoice",
            layer="code",
            language="python",
            file="app.py",
            line=1,
            end_line=4,
        )
    )
    graph.add_node(
        Node(
            id="tbl:invoices",
            kind="table",
            name="invoices",
            layer="data",
            language="sql",
            file="sql/schema.sql",
            line=1,
            end_line=3,
            detail={"columns": ["id"]},
        )
    )
    graph.add_edge(
        "fn:app.py:create_invoice",
        "tbl:invoices",
        "touches",
        "linked",
        [Evidence("app.py", 2, "INSERT INTO invoices")],
    )
    return graph
