const configuredApiBase = import.meta.env.VITE_API_BASE_URL?.trim();
const hostApiBase = typeof window === "undefined"
  ? "http://localhost:8000"
  : `${window.location.protocol}//${window.location.hostname}:8000`;

export const API_BASE = (configuredApiBase || hostApiBase).replace(/\/$/, "");

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`);
  if (!response.ok) throw new Error(`Backend returned ${response.status} for ${path}.`);
  return response.json() as Promise<T>;
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    let detail = `Backend returned ${response.status}.`;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.error_message || detail;
    } catch { /* Keep the HTTP status message. */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}
