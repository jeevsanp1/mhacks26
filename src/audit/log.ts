import { appendFileSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const DATA_PATH = new URL("../../data/audit-log.jsonl", import.meta.url).pathname;

export function auditLog(entry: Record<string, unknown>): void {
  mkdirSync(dirname(DATA_PATH), { recursive: true });
  const line = JSON.stringify({ at: new Date().toISOString(), ...entry });
  appendFileSync(DATA_PATH, line + "\n");
}
