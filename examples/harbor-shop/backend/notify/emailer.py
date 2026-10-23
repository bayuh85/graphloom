"""Receipt delivery for Harbor Supply."""


def queue_receipt(email: str, invoice_id: str) -> None:
    send_receipt(email, invoice_id)


def send_receipt(email: str, invoice_id: str) -> None:
    endpoint = smtp_endpoint({"smtp": {"host": "localhost", "port": 1025}})
    _deliver(endpoint, email, invoice_id)


def smtp_endpoint(settings: dict) -> str:
    host = settings["smtp"]["host"]
    port = settings["smtp"]["port"]
    return host + ":" + str(port)


def _deliver(endpoint: str, email: str, invoice_id: str) -> None:
    return None
