# Billing

Harbor Supply writes one invoice per checkout and then a receipt.

## Creating an invoice

`post_invoice` is the HTTP entry. It calls `create_invoice`, which prices the
sku with `price_for`, holds stock with `reserve_stock`, and inserts a row in
the `invoices` table. The in-memory draft is `InvoiceDraft`. The customer
check reads `customers`.

## Taking payment

`record_payment` inserts into `payments` and calls `queue_receipt`.
Delivery is `send_receipt`, which reads the `smtp.host` setting before it
hands the message to the mailer.

## Catalog

Shelf prices come from the `products` table through `list_products`.
