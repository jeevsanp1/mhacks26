import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

const STATE_PATH = new URL("../../data/trusted-contact-notifications.json", import.meta.url).pathname;
const MAX_PER_DAY = 5;

interface State {
  sentAt: string[];
}

function load(): State {
  if (!existsSync(STATE_PATH)) return { sentAt: [] };
  return JSON.parse(readFileSync(STATE_PATH, "utf-8"));
}

function save(state: State): void {
  mkdirSync(dirname(STATE_PATH), { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}

/** Backend-enforced rate limit so a confused or manipulated agent can't spam a
 * trusted contact into ignoring the one notification that matters. */
export function notifyTrustedContact(message: string, reason: string): { ok: true } | { ok: false; reason: string } {
  const state = load();
  const since = Date.now() - 24 * 60 * 60 * 1000;
  const recent = state.sentAt.filter((t) => new Date(t).getTime() > since);

  if (recent.length >= MAX_PER_DAY) {
    return { ok: false, reason: `Already sent ${MAX_PER_DAY} notifications to the trusted contact in the last 24 hours.` };
  }

  // Stub: wire this up to SMS/email (e.g. Twilio) once a real trusted-contact
  // record exists. Logged here so the demo has something observable.
  console.log(`[notify_trusted_contact] reason=${reason} message=${message}`);

  recent.push(new Date().toISOString());
  save({ sentAt: recent });
  return { ok: true };
}
