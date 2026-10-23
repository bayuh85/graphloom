import { fetchProducts } from "./api.ts";

export function renderCart(items) {
  return items.map((item) => item.sku).join("\n");
}

export async function loadCatalog() {
  const products = await fetchProducts();
  return renderCart(products);
}

export async function submitInvoice(cart) {
  const response = await fetch("/invoices", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(cart),
  });
  const invoice = await response.json();
  renderCart(invoice.lines || []);
  return invoice;
}
