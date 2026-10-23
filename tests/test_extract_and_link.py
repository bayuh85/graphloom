"""Unit tests for extractors and the offline linker."""

from graphloom.build import build_repository
from graphloom.extract_config import parse_json_keys, parse_toml_keys, parse_yaml_keys
from graphloom.model import Graph
from graphloom.util import absolute_import


def test_absolute_import_levels():
    file = "backend/billing/payments.py"
    assert absolute_import(file, 0, "backend.notify.emailer") == "backend.notify.emailer"
    assert absolute_import(file, 1, None) == "backend.billing"
    assert absolute_import(file, 1, "helpers") == "backend.billing.helpers"
    assert absolute_import(file, 2, "notify.emailer") == "backend.notify.emailer"
    init = "backend/notify/__init__.py"
    assert absolute_import(init, 1, "emailer") == "backend.notify.emailer"


def test_config_parsers_cover_nested_keys():
    toml_keys = dict(parse_toml_keys('[smtp]\nhost = "localhost"\nport = 1025\n'))
    assert toml_keys["smtp.host"] == 2
    assert toml_keys["smtp.port"] == 3
    yaml_keys = dict(parse_yaml_keys("billing:\n  retries: 3\n  receipt: true\n"))
    assert yaml_keys["billing.retries"] == 2
    assert "billing.receipt" in yaml_keys
    json_keys = dict(parse_json_keys('{\n  "limits": {\n    "max_lines": 40\n  }\n}\n'))
    assert json_keys["limits.max_lines"] == 3


def test_relative_import_and_call_are_parsed(tmp_path):
    pkg = tmp_path / "shop"
    (pkg / "notify").mkdir(parents=True)
    (pkg / "notify" / "emailer.py").write_text(
        "def send_receipt(email: str) -> None:\n    return None\n",
        encoding="utf-8",
    )
    (pkg / "billing").mkdir()
    (pkg / "billing" / "payments.py").write_text(
        "from ..notify.emailer import send_receipt\n\n"
        "def record_payment(email: str) -> None:\n"
        "    send_receipt(email)\n",
        encoding="utf-8",
    )
    graph = build_repository(pkg)
    edge = _edge(graph, "invokes", "record_payment", "send_receipt")
    assert edge is not None
    assert edge.provenance == "parsed"
    assert edge.evidence[0].line == 4


def test_unique_name_call_is_linked_not_parsed(tmp_path):
    (tmp_path / "mail.py").write_text(
        "def send_receipt(email: str) -> None:\n    return None\n",
        encoding="utf-8",
    )
    (tmp_path / "checkout.py").write_text(
        "def checkout(email: str) -> None:\n    send_receipt(email)\n",
        encoding="utf-8",
    )
    graph = build_repository(tmp_path)
    edge = _edge(graph, "invokes", "checkout", "send_receipt")
    assert edge is not None
    assert edge.provenance == "linked"


def test_syntax_error_does_not_abort_the_build(tmp_path):
    (tmp_path / "broken.py").write_text("def (\n", encoding="utf-8")
    (tmp_path / "ok.py").write_text(
        "def ready() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    graph = build_repository(tmp_path)
    assert any(node.name == "ready" for node in graph.nodes.values())
    assert any("syntax error" in warning for warning in graph.meta["warnings"])


def test_sql_foreign_key_is_parsed(tmp_path):
    (tmp_path / "ledger.sql").write_text(
        "CREATE TABLE customers (id INTEGER PRIMARY KEY);\n"
        "CREATE TABLE invoices (\n"
        "  id INTEGER PRIMARY KEY,\n"
        "  customer_id INTEGER REFERENCES customers(id)\n"
        ");\n",
        encoding="utf-8",
    )
    graph = build_repository(tmp_path)
    invoices = graph.nodes["tbl:invoices"]
    assert invoices.detail["columns"] == ["id", "customer_id"]
    edge = graph.edges["e:ties:tbl:invoices->tbl:customers"]
    assert edge.provenance == "parsed"


def test_markdown_fence_hides_false_headings(tmp_path):
    (tmp_path / "notes.md").write_text(
        "# Real\n\nThe handler is `ready`.\n\n```\n# not a heading\n```\n\n## Next\n\nMore.\n",
        encoding="utf-8",
    )
    (tmp_path / "ok.py").write_text(
        "def ready() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    graph = build_repository(tmp_path)
    titles = [node.name for node in graph.nodes.values() if node.kind == "heading"]
    assert titles == ["Real", "Next"]
    mentions = [
        edge
        for edge in graph.edges.values()
        if edge.relation == "mentions" and edge.target.endswith(":ready")
    ]
    assert len(mentions) == 1
    assert mentions[0].provenance == "linked"


def _edge(graph: Graph, relation: str, source_name: str, target_name: str):
    sources = [node.id for node in graph.nodes.values() if node.name == source_name]
    targets = [node.id for node in graph.nodes.values() if node.name == target_name]
    for edge in graph.edges.values():
        if edge.relation == relation and edge.source in sources and edge.target in targets:
            return edge
    return None
