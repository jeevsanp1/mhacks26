import { env } from "./config.js";

const VAPI_URL = "https://api.vapi.ai";

export async function vapi<T>(method: "GET" | "POST" | "PATCH" | "DELETE", path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${VAPI_URL}${path}`, {
    method,
    headers: { Authorization: `Bearer ${env.VAPI_API_KEY}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`Vapi ${method} ${path} failed: ${res.status} ${text}`);
  return JSON.parse(text) as T;
}
