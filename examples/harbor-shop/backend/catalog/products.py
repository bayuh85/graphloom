"""Catalog prices and stock holds."""


def price_for(sku: str) -> int:
    statement = "SELECT price_cents FROM products WHERE sku = ?"
    return _scalar(statement, sku)


def reserve_stock(sku: str, qty: int) -> None:
    statement = "UPDATE products SET held = held + ? WHERE sku = ?"
    _exec(statement, (qty, sku))


def list_products() -> list:
    statement = "SELECT sku, name, price_cents FROM products WHERE active = 1"
    return _rows(statement)


def _scalar(statement: str, sku: str) -> int:
    return 0


def _exec(statement: str, row: tuple) -> None:
    return None


def _rows(statement: str) -> list:
    return []
