"""Invoice drafts for Harbor Supply."""

from backend.billing.payments import record_payment
from backend.catalog.products import price_for, reserve_stock


class InvoiceDraft:
    """A row that has not been written yet."""

    def __init__(self, customer_id: int, sku: str, total_cents: int) -> None:
        self.customer_id = customer_id
        self.sku = sku
        self.total_cents = total_cents

    def as_row(self) -> tuple:
        return (self.customer_id, self.sku, self.total_cents, "open")


def format_total(cents: int, settings: dict) -> str:
    currency = settings["shop"]["currency"]
    cap = settings["limits"]["max_lines"]
    return currency + " " + str(cents / 100) + " cap " + str(cap)


def create_invoice(payload: dict, settings: dict) -> dict:
    sku = str(payload["sku"])
    qty = int(payload.get("qty", 1))
    total = price_for(sku) * qty
    reserve_stock(sku, qty)
    lookup = "SELECT id, email FROM customers WHERE email = ?"
    _lookup(lookup, str(payload["email"]))
    draft = InvoiceDraft(int(payload["customer_id"]), sku, total)
    row = draft.as_row()
    statement = (
        "INSERT INTO invoices (customer_id, sku, total_cents, status) "
        "VALUES (?, ?, ?, ?)"
    )
    _write_invoice(statement, row)
    record_payment(str(payload["invoice_id"]), total, "card", str(payload["email"]))
    return {
        "invoice_id": payload["invoice_id"],
        "total_cents": total,
        "label": format_total(total, settings),
    }


def _lookup(statement: str, email: str) -> int:
    return 0


def _write_invoice(statement: str, row: tuple) -> None:
    return None
