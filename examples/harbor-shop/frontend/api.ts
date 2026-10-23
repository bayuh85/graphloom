export async function fetchProducts(): Promise<unknown> {
  const response = await fetch("/products", { method: "GET" });
  return response.json();
}
