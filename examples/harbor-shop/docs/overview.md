# Harbor Supply

Harbor Supply sells from a single warehouse. The service prices a sku, writes
an invoice, captures a payment, and mails a receipt.

## Request path

The browser file `frontend/cart.js` posts the cart. `submitInvoice` hits
`post_invoice`. Product lists come from `fetchProducts` in `frontend/api.ts`.

## Where the rows live

Tables are declared in `sql/schema.sql`. Mail settings live in
`config/settings.toml`. The unwired logger is `log_event`.
