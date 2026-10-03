import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export interface MemoryItem {
  topic: string;
  value: string;
  updatedAt: string;
}

type MemoryFile = Record<string, Record<string, MemoryItem>>;

const DATA_PATH = new URL("../../data/memory.json", import.meta.url).pathname;

function load(): MemoryFile {
  if (!existsSync(DATA_PATH)) return {};
  return JSON.parse(readFileSync(DATA_PATH, "utf-8"));
}

function save(data: MemoryFile): void {
  mkdirSync(dirname(DATA_PATH), { recursive: true });
  writeFileSync(DATA_PATH, JSON.stringify(data, null, 2));
}

/**
 * File-backed so the person (or a trusted family member) can open data/memory.json
 * directly to view, correct, or delete what's stored — this is the source of truth,
 * never the voice agent's own built-in memory.
 */
export const memoryStore = {
  get(userId: string, topic: string): MemoryItem | undefined {
    return load()[userId]?.[topic];
  },

  list(userId: string): MemoryItem[] {
    return Object.values(load()[userId] ?? {});
  },

  set(userId: string, topic: string, value: string): MemoryItem {
    const data = load();
    data[userId] ??= {};
    const item: MemoryItem = { topic, value, updatedAt: new Date().toISOString() };
    data[userId][topic] = item;
    save(data);
    return item;
  },

  delete(userId: string, topic: string): boolean {
    const data = load();
    if (!data[userId]?.[topic]) return false;
    delete data[userId][topic];
    save(data);
    return true;
  },
};
