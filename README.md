# Graphloom

Coding agents answer architecture questions by grepping and reading file after file. They burn tokens and still miss how the repository fits together: which module calls which function, which doc names which symbol, which handler touches which table. Graphloom weaves that map once, offline, and lets an agent ask a short question against it.

## What it does

`graphloom build` walks a repository and writes three files under `.graphloom/`:

| File | Role |
| --- | --- |
| `graph.json` | Versioned graph (`graphloom.graph/v1`). Every edge has a provenance tag and a `file:line` excerpt. |
| `REPORT.md` | Hubs, isolated modules, cross-layer links, unread config keys, and questions the graph can already answer. |
| `graph.html` | One self-contained viewer. Search, filter, click a node. No CDN, no network. |

You then query that file instead of the tree:

- `graphloom query <term>` — matching nodes and their neighbors, trimmed to a token budget
- `graphloom path <a> <b>` — shortest path with evidence on each hop
- `graphloom explain <node>` — one node, every edge
- `graphloom ask "question"` — a small retrieved subgraph plus an LLM answer that cites node ids and `file:line`
- `graphloom serve` — MCP stdio server so Cursor or Claude Code can call the same tools

The offline build understands:

- **Python** via the stdlib `ast` module: modules, classes, functions, imports, and calls
- **JavaScript / TypeScript** with a small scanner: imports, exported functions and classes, calls, `fetch` URLs
- **SQL** `CREATE TABLE` nodes, plus foreign keys
- **Markdown** headings, with edges to symbols written in backticks
- **TOML / YAML / JSON** keys, with edges from code that reads them

An optional `--semantic` pass sends documentation chunks, not source code, to a model and grounds the concepts it returns onto nodes that already exist.

## Quickstart

```bash
pip install -e .
cd examples/harbor-shop
graphloom build .
graphloom query invoices
graphloom path post_invoice send_receipt
```

Open `.graphloom/graph.html` in a browser. The sample project is Harbor Supply, a tiny wholesale checkout: Python services, a SQL ledger, a browser cart, and two docs. From another directory, pass `--graph path/to/.graphloom/graph.json`.

Real output from that example:

```text
$ graphloom build .
graphloom build .
nodes: 67
edges: 108
  parsed: 75
  linked: 33
  llm: 0
wrote graph: .graphloom/graph.json
wrote report: .graphloom/REPORT.md
wrote html: .graphloom/graph.html
```

```text
$ graphloom query invoices
query: invoices
matches: 10
showing: 6 nodes, depth 1
budget: 1600 tokens (~1494 used)
truncated: yes

## tbl:invoices
kind: table   layer: data   name: invoices
at: sql/schema.sql:17
columns: id, customer_id, sku, total_cents, status
edges:
  -> ties [parsed] tbl:customers (customers)  sql/schema.sql:17  CREATE TABLE invoices (
  -> ties [parsed] tbl:products (products)  sql/schema.sql:17  CREATE TABLE invoices (
  <- contains [parsed] sql:sql/schema.sql (sql/schema.sql)  sql/schema.sql:17  CREATE TABLE invoices (
  <- mentions [linked] doc:docs/billing.md:h:creating-an-invoice (Creating an invoice)  docs/billing.md:9  the `invoices` table. The in-memory draft is `InvoiceDraft`. The customer
  <- ties [parsed] tbl:payments (payments)  sql/schema.sql:25  CREATE TABLE payments (
  <- touches [linked] fn:backend/billing/invoices.py:create_invoice (create_invoice)  backend/billing/invoices.py:35  "INSERT INTO invoices (customer_id, sku, total_cents, status) "

## mod:backend/billing/invoices.py
kind: module   layer: code   name: backend.billing.invoices
at: backend/billing/invoices.py:1
edges:
  -> contains [parsed] cls:backend/billing/invoices.py:InvoiceDraft (InvoiceDraft)  backend/billing/invoices.py:7  class InvoiceDraft:
  -> contains [parsed] fn:backend/billing/invoices.py:_lookup (_lookup)  backend/billing/invoices.py:47  def _lookup(statement: str, email: str) -> int:
  -> contains [parsed] fn:backend/billing/invoices.py:_write_invoice (_write_invoice)  backend/billing/invoices.py:51  def _write_invoice(statement: str, row: tuple) -> None:
  -> contains [parsed] fn:backend/billing/invoices.py:create_invoice (create_invoice)  backend/billing/invoices.py:25  def create_invoice(payload: dict, settings: dict) -> dict:
  -> contains [parsed] fn:backend/billing/invoices.py:format_total (format_total)  backend/billing/invoices.py:19  def format_total(cents: int, settings: dict) -> str:
  -> imports [parsed] fn:backend/billing/payments.py:record_payment (record_payment)  backend/billing/invoices.py:3  from backend.billing.payments import record_payment
  -> imports [parsed] fn:backend/catalog/products.py:price_for (price_for)  backend/billing/invoices.py:4  from backend.catalog.products import price_for, reserve_stock
  -> imports [parsed] fn:backend/catalog/products.py:reserve_stock (reserve_stock)  backend/billing/invoices.py:4  from backend.catalog.products import price_for, reserve_stock

## tbl:customers
kind: table   layer: data   name: customers
at: sql/schema.sql:3
columns: id, email, name
edges:
  <- contains [parsed] sql:sql/schema.sql (sql/schema.sql)  sql/schema.sql:3  CREATE TABLE customers (
  <- mentions [linked] doc:docs/billing.md:h:creating-an-invoice (Creating an invoice)  docs/billing.md:10  check reads `customers`.
  <- ties [parsed] tbl:invoices (invoices)  sql/schema.sql:17  CREATE TABLE invoices (
  <- touches [linked] fn:backend/billing/invoices.py:create_invoice (create_invoice)  backend/billing/invoices.py:30  lookup = "SELECT id, email FROM customers WHERE email = ?"

## tbl:products
kind: table   layer: data   name: products
at: sql/schema.sql:9
columns: sku, name, price_cents, held, active
edges:
  <- contains [parsed] sql:sql/schema.sql (sql/schema.sql)  sql/schema.sql:9  CREATE TABLE products (
  <- mentions [linked] doc:docs/billing.md:h:catalog (Catalog)  docs/billing.md:20  Shelf prices come from the `products` table through `list_products`.
  <- ties [parsed] tbl:invoices (invoices)  sql/schema.sql:17  CREATE TABLE invoices (
  <- touches [linked] fn:backend/catalog/products.py:list_products (list_products)  backend/catalog/products.py:15  statement = "SELECT sku, name, price_cents FROM products WHERE active = 1"
  <- touches [linked] fn:backend/catalog/products.py:price_for (price_for)  backend/catalog/products.py:5  statement = "SELECT price_cents FROM products WHERE sku = ?"
  <- touches [linked] fn:backend/catalog/products.py:reserve_stock (reserve_stock)  backend/catalog/products.py:10  statement = "UPDATE products SET held = held + ? WHERE sku = ?"

## sql:sql/schema.sql
kind: sqlscript   layer: data   name: sql/schema.sql
at: sql/schema.sql:1
edges:
  -> contains [parsed] tbl:customers (customers)  sql/schema.sql:3  CREATE TABLE customers (
  -> contains [parsed] tbl:invoices (invoices)  sql/schema.sql:17  CREATE TABLE invoices (
  -> contains [parsed] tbl:payments (payments)  sql/schema.sql:25  CREATE TABLE payments (
  -> contains [parsed] tbl:products (products)  sql/schema.sql:9  CREATE TABLE products (
  <- mentions [linked] doc:README.md:h:harbor-supply (Harbor Supply)  README.md:8  Tables are declared in `sql/schema.sql`.
  <- mentions [linked] doc:docs/overview.md:h:where-the-rows-live (Where the rows live)  docs/overview.md:13  Tables are declared in `sql/schema.sql`. Mail settings live in

## doc:docs/billing.md:h:creating-an-invoice
kind: heading   layer: docs   name: Creating an invoice
at: docs/billing.md:5
edges:
  -> mentions [linked] cls:backend/billing/invoices.py:InvoiceDraft (InvoiceDraft)  docs/billing.md:9  the `invoices` table. The in-memory draft is `InvoiceDraft`. The customer
  -> mentions [linked] fn:backend/app.py:post_invoice (post_invoice)  docs/billing.md:7  `post_invoice` is the HTTP entry. It calls `create_invoice`, which prices the
  -> mentions [linked] fn:backend/billing/invoices.py:create_invoice (create_invoice)  docs/billing.md:7  `post_invoice` is the HTTP entry. It calls `create_invoice`, which prices the
  -> mentions [linked] fn:backend/catalog/products.py:price_for (price_for)  docs/billing.md:8  sku with `price_for`, holds stock with `reserve_stock`, and inserts a row in
  -> mentions [linked] fn:backend/catalog/products.py:reserve_stock (reserve_stock)  docs/billing.md:8  sku with `price_for`, holds stock with `reserve_stock`, and inserts a row in
  -> mentions [linked] tbl:customers (customers)  docs/billing.md:10  check reads `customers`.
  -> mentions [linked] tbl:invoices (invoices)  docs/billing.md:9  the `invoices` table. The in-memory draft is `InvoiceDraft`. The customer
```

```text
$ graphloom path post_invoice send_receipt
path: post_invoice -> send_receipt
resolved: fn:backend/app.py:post_invoice -> fn:backend/notify/emailer.py:send_receipt
hops: 4
directed: yes

1. fn:backend/app.py:post_invoice
   function post_invoice
   backend/app.py:9
   invokes [parsed] -> fn:backend/billing/invoices.py:create_invoice
   backend/app.py:10  return create_invoice(payload, settings)

2. fn:backend/billing/invoices.py:create_invoice
   function create_invoice
   backend/billing/invoices.py:25
   invokes [parsed] -> fn:backend/billing/payments.py:record_payment
   backend/billing/invoices.py:39  record_payment(str(payload["invoice_id"]), total, "card", str(payload["email"]))

3. fn:backend/billing/payments.py:record_payment
   function record_payment
   backend/billing/payments.py:6
   invokes [parsed] -> fn:backend/notify/emailer.py:queue_receipt
   backend/billing/payments.py:12  queue_receipt(email, invoice_id)

4. fn:backend/notify/emailer.py:queue_receipt
   function queue_receipt
   backend/notify/emailer.py:4
   invokes [parsed] -> fn:backend/notify/emailer.py:send_receipt
   backend/notify/emailer.py:5  send_receipt(email, invoice_id)

5. fn:backend/notify/emailer.py:send_receipt
   function send_receipt
   backend/notify/emailer.py:8
```

The query stopped inside the token budget (`truncated: yes`): the `invoices` table is touched by `create_invoice`, tied to `customers`, `products`, and `payments`, and named from the billing doc. The path from the HTTP route to the mailer is four parsed calls.

`graphloom ask` and `graphloom build --semantic` need an API key. The build, the viewer, and `query` / `path` / `explain` do not.

```bash
export ATMOROUTER_API_KEY=ar-your-key-here
graphloom build . --semantic --max-calls 4
graphloom ask "what touches the invoices table?"
```

## Provenance

Every edge is tagged so an agent can tell a fact the parser read from a guess.

| Tag | Meaning |
| --- | --- |
| `parsed` | Read from syntax. An import binding, a call to a function defined in the same file, a `CREATE TABLE` / foreign key, or a literal `settings["smtp"]["host"]` path. |
| `linked` | Resolved afterward. A backtick in a doc, a table name inside a SQL string, a unique function name with no import, a `fetch("/invoices")` matched to a route. |
| `llm` | Added by the semantic pass. The model proposed a concept and an anchor; Graphloom kept the anchor only when it names a node that already exists. |

Evidence is a list of `{file, line, excerpt}` on the edge. `explain` and `path` print it. The viewer shows it in the side panel.

Relations used in the graph: `contains`, `imports`, `invokes`, `touches` (code to a table), `reads` (code to a config key), `mentions` (a heading to a symbol), `hits` (a client `fetch` to a route), `ties` (a foreign key), `about` (a concept to a node).

## AtmoRouter

The code graph is built free and offline. Only two things ever call a model: the optional semantic pass over documentation, and `graphloom ask`. Both send a short prompt (doc chunks, or a handful of nodes), not the repository. Routing those calls through a cheap AI API gateway keeps the bill small, which is why Graphloom defaults to [AtmoRouter](https://atmorouter.dev), an OpenRouter alternative with an OpenAI-compatible `/v1/chat/completions` API.

Set `GRAPHLOOM_BASE_URL` and `GRAPHLOOM_API_KEY` to point the same client at any other OpenAI-compatible endpoint. Set `GRAPHLOOM_API_STYLE=anthropic` to POST `{base}/messages` instead.

The default model is `zai/glm-5.3-flash`, a low-cost text model on the AtmoRouter catalog (`GET https://atmorouter.dev/v1/models`). Override `GRAPHLOOM_MODEL` if you want a different id.

Calls are batched, cached by a hash of the chunk, and stopped at `--max-calls` (default 8). A second semantic build of an unchanged doc does not call the model again.

Powered by [AtmoRouter](https://atmorouter.dev).

## Configuration

Graphloom reads a `.env` file in the working directory if present, and does not override variables that are already set. Copy `.env.example` to start.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ATMOROUTER_API_KEY` | empty | API key used when `GRAPHLOOM_API_KEY` is unset. |
| `GRAPHLOOM_API_KEY` | empty | Wins over `ATMOROUTER_API_KEY`. |
| `GRAPHLOOM_BASE_URL` | `https://atmorouter.dev/v1` | OpenAI-compatible base URL. |
| `GRAPHLOOM_MODEL` | `zai/glm-5.3-flash` | Model id for `--semantic` and `ask`. |
| `GRAPHLOOM_API_STYLE` | `openai` | `openai` posts to `{base}/chat/completions`. `anthropic` posts to `{base}/messages`. |

`.env.example` contains `ATMOROUTER_API_KEY=ar-your-key-here`.

## Cursor and Claude Code

Build the graph first (`graphloom build .`), then point the MCP server at it. The server speaks JSON-RPC over stdio (newline-delimited JSON, as the MCP spec requires; Content-Length framing is also accepted): `initialize`, `notifications/initialized`, `tools/list`, and `tools/call`. The tools are `graph_query`, `graph_path`, `graph_explain`, and `graph_ask`.

Cursor, `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "graphloom": {
      "command": "graphloom",
      "args": ["serve", "--graph", ".graphloom/graph.json"]
    }
  }
}
```

Claude Code, `.mcp.json` in the project:

```json
{
  "mcpServers": {
    "graphloom": {
      "command": "graphloom",
      "args": ["serve", "--graph", ".graphloom/graph.json"]
    }
  }
}
```

`graph_ask` uses the environment above. The other three tools stay offline.

## Development

Requires Python 3.10+. Runtime dependencies are the standard library only (`urllib` for HTTP).

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

GitHub Actions runs `ruff` and `pytest` on Python 3.10 and 3.12.

## License

MIT. See [LICENSE](LICENSE).
