"""The Harbor Supply example is the quickstart fixture."""

import json

import pytest

from graphloom.build import build_repository, write_outputs
from graphloom.query import render_explain, render_path, render_query
from graphloom.report import render_report
from graphloom.viewer import render_html
from paths import EXAMPLE

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def shop(tmp_path_factory):
    graph = build_repository(EXAMPLE)
    out = tmp_path_factory.mktemp("harbor")
    written = write_outputs(graph, out)
    return graph, written


def test_checkout_path_is_directed_and_cited(shop):
    graph, _written = shop
    text = render_path(graph, "post_invoice", "send_receipt")
    assert "directed: yes" in text
    assert "hops: 4" in text
    steps = text.split("directed: yes", 1)[1]
    names = (
        "fn:backend/app.py:post_invoice",
        "fn:backend/billing/invoices.py:create_invoice",
        "fn:backend/billing/payments.py:record_payment",
        "fn:backend/notify/emailer.py:queue_receipt",
        "fn:backend/notify/emailer.py:send_receipt",
    )
    positions = [steps.index(name) for name in names]
    assert positions == sorted(positions)
    assert "[parsed]" in text
    assert "backend/app.py:" in text


def test_invoices_query_cites_the_writer(shop):
    graph, _written = shop
    text = render_query(graph, "invoices", budget=1600, depth=1)
    assert "tbl:invoices" in text
    assert "create_invoice" in text
    assert "touches [linked]" in text
    assert "matches:" in text
    assert "budget: 1600" in text


def test_table_touch_doc_mention_config_read_and_route_hit(shop):
    graph, _written = shop
    assert _has(graph, "touches", "create_invoice", "invoices", "linked")
    assert _has(graph, "mentions", "Creating an invoice", "create_invoice", "linked")
    assert _has(graph, "mentions", "Creating an invoice", "invoices", "linked")
    assert _has(graph, "reads", "format_total", "shop.currency", "parsed")
    assert _has(graph, "reads", "smtp_endpoint", "smtp.host", "parsed")
    assert _has(graph, "hits", "submitInvoice", "post_invoice", "linked")
    assert _has(graph, "hits", "fetchProducts", "get_products", "linked")
    assert _has(graph, "imports", "frontend/cart.js", "fetchProducts", "parsed")
    assert _has(graph, "invokes", "create_invoice", "as_row", "linked")
    assert _has(graph, "invokes", "post_invoice", "create_invoice", "parsed")
    assert _has(graph, "ties", "invoices", "customers", "parsed")
    assert _has(graph, "ties", "payments", "invoices", "parsed")


def test_every_edge_has_provenance_and_evidence(shop):
    graph, _written = shop
    assert graph.to_dict()["schema"] == "graphloom.graph/v1"
    assert graph.edges, "example graph should not be empty"
    for edge in graph.edges.values():
        assert edge.provenance in {"parsed", "linked", "llm"}
        assert edge.evidence
        assert edge.evidence[0].file
        assert edge.evidence[0].line >= 1


def test_report_calls_out_hubs_isolation_and_a_real_path(shop):
    graph, written = shop
    report = written["report"].read_text(encoding="utf-8")
    assert "## Hubs" in report
    assert "backend/audit.py" in report
    assert "graphloom path post_invoice send_receipt" in report
    assert "billing.retries" in report
    assert "smtp.host" not in report.split("## Unread config keys", 1)[1].split("##", 1)[0]
    # The same text is what render_report returns.
    assert "Isolated modules" in render_report(graph)


def test_html_viewer_is_offline_and_contains_the_graph(shop):
    graph, written = shop
    page = written["html"].read_text(encoding="utf-8")
    assert page == render_html(graph)
    assert "<canvas" in page
    assert "id=\"q\"" in page
    assert "tbl:invoices" in page
    lowered = page.lower()
    for banned in ("cdn.", "unpkg", "jsdelivr", "googleapis", "cdnjs"):
        assert banned not in lowered
    data = page.split('id="loom-data"', 1)[1]
    data = data.split(">", 1)[1].split("</script>", 1)[0]
    payload = json.loads(data)
    assert payload["counts"]["nodes"] == len(graph.nodes)
    assert payload["counts"]["edges"] == len(graph.edges)


def test_explain_shows_the_route(shop):
    graph, _written = shop
    text = render_explain(graph, "post_invoice")
    assert "route: POST /invoices" in text
    assert "create_invoice" in text
    assert "invokes [parsed]" in text


def test_tiny_budget_truncates(shop):
    graph, _written = shop
    text = render_query(graph, "invoices", budget=40, depth=2)
    assert "truncated: yes" in text
    assert "tbl:invoices" in text


def _has(graph, relation, source_name, target_name, provenance):
    for edge in graph.edges.values():
        if edge.relation != relation or edge.provenance != provenance:
            continue
        source = graph.nodes[edge.source]
        target = graph.nodes[edge.target]
        if source.name == source_name and target.name == target_name:
            return True
    return False
