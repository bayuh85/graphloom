"""HTTP entrypoints for Harbor Supply."""

from backend.billing.invoices import create_invoice
from backend.catalog.products import list_products
from backend.http import route


@route("POST", "/invoices")
def post_invoice(payload: dict, settings: dict) -> dict:
    return create_invoice(payload, settings)


@route("GET", "/products")
def get_products() -> list:
    return list_products()
