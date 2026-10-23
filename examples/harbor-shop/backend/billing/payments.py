"""Card capture and the handoff to receipt mail."""

from backend.notify.emailer import queue_receipt


def record_payment(invoice_id: str, amount_cents: int, provider: str, email: str) -> str:
    statement = (
        "INSERT INTO payments (invoice_id, amount_cents, provider) VALUES (?, ?, ?)"
    )
    payment_id = "pay_" + invoice_id
    _exec(statement, (invoice_id, amount_cents, provider))
    queue_receipt(email, invoice_id)
    return payment_id


def _exec(statement: str, row: tuple) -> None:
    return None
