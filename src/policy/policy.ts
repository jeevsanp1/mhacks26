import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";

export type Tier = "free" | "light" | "strong";

export type ActionKind = "calendar_edit" | "payment" | "share_personal_info";

export interface ActionRequest {
  userId: string;
  kind: ActionKind;
  /** Free-form details specific to the kind, e.g. { payeeId, amountCents, date } for payments. */
  details: Record<string, unknown>;
}

export interface PendingAction {
  token: string;
  userId: string;
  kind: ActionKind;
  details: Record<string, unknown>;
  tier: Tier;
  consequenceStatement: string;
  createdAt: number;
  expiresAt: number;
  used: boolean;
}

interface Payee {
  id: string;
  name: string;
  allowlistedAt: string;
}

const PAYEES_PATH = new URL("../../data/allowlisted-payees.json", import.meta.url).pathname;

const POLICY_LIMITS = {
  maxPaymentCents: 50_000, // $500 per payment, backend-enforced regardless of what the agent "decides"
  maxDailyPaymentCents: 100_000, // $1,000/day across all payments
  newPayeeCoolingOffHours: 24,
};

/** In-memory only — fine for a single-process demo. A real deployment needs a
 * shared/persistent store (Redis, DB row with TTL) since tokens must survive
 * restarts and work across multiple server instances. */
const pendingActions = new Map<string, PendingAction>();
const PENDING_TTL_MS = 5 * 60 * 1000;

function loadPayees(): Payee[] {
  if (!existsSync(PAYEES_PATH)) return [];
  return JSON.parse(readFileSync(PAYEES_PATH, "utf-8"));
}

export function allowlistPayee(id: string, name: string): Payee {
  const payees = loadPayees();
  const payee: Payee = { id, name, allowlistedAt: new Date().toISOString() };
  const next = [...payees.filter((p) => p.id !== id), payee];
  mkdirSync(dirname(PAYEES_PATH), { recursive: true });
  writeFileSync(PAYEES_PATH, JSON.stringify(next, null, 2));
  return payee;
}

function tierForKind(kind: ActionKind): Tier {
  switch (kind) {
    case "calendar_edit":
      return "light";
    case "payment":
    case "share_personal_info":
      return "strong";
  }
}

function describeConsequence(kind: ActionKind, details: Record<string, unknown>): string {
  switch (kind) {
    case "calendar_edit":
      return `I'll add "${details.title}" to your calendar on ${details.date}.`;
    case "payment": {
      const amount = ((details.amountCents as number) / 100).toFixed(2);
      return `This pays $${amount} to ${details.payeeName} from your account on ${details.date}.`;
    }
    case "share_personal_info":
      return `This shares "${details.fieldName}" with ${details.recipient}.`;
  }
}

export interface Evaluation {
  allowed: boolean;
  reason?: string;
  tier: Tier;
}

/** Everything that can say no lives here, outside the model, so a confirmation
 * token is never issued for something the model talked itself into. */
export function evaluate(request: ActionRequest): Evaluation {
  const tier = tierForKind(request.kind);

  if (request.kind === "share_personal_info") {
    return { allowed: false, reason: "This agent never shares passwords, SSNs, or other identity secrets.", tier };
  }

  if (request.kind === "payment") {
    const { payeeId, amountCents } = request.details as { payeeId: string; amountCents: number };
    const payee = loadPayees().find((p) => p.id === payeeId);
    if (!payee) {
      return { allowed: false, reason: "That payee isn't on your allowlist yet. A trusted contact needs to add them first.", tier };
    }
    const hoursSinceAllowlisted = (Date.now() - new Date(payee.allowlistedAt).getTime()) / 36e5;
    if (hoursSinceAllowlisted < POLICY_LIMITS.newPayeeCoolingOffHours) {
      return { allowed: false, reason: `New payees need a ${POLICY_LIMITS.newPayeeCoolingOffHours}-hour cooling-off period before their first payment.`, tier };
    }
    if (amountCents > POLICY_LIMITS.maxPaymentCents) {
      return { allowed: false, reason: `That's over the $${(POLICY_LIMITS.maxPaymentCents / 100).toFixed(0)} per-payment limit.`, tier };
    }
  }

  return { allowed: true, tier };
}

export function prepareAction(request: ActionRequest): { ok: true; pending: PendingAction } | { ok: false; reason: string } {
  const evaluation = evaluate(request);
  if (!evaluation.allowed) {
    return { ok: false, reason: evaluation.reason ?? "That action isn't allowed." };
  }

  const now = Date.now();
  const pending: PendingAction = {
    token: randomUUID(),
    userId: request.userId,
    kind: request.kind,
    details: request.details,
    tier: evaluation.tier,
    consequenceStatement: describeConsequence(request.kind, request.details),
    createdAt: now,
    expiresAt: now + PENDING_TTL_MS,
    used: false,
  };
  pendingActions.set(pending.token, pending);
  return { ok: true, pending };
}

export function consumeAction(token: string): { ok: true; pending: PendingAction } | { ok: false; reason: string } {
  const pending = pendingActions.get(token);
  if (!pending) return { ok: false, reason: "Unknown or already-used confirmation token." };
  if (pending.used) return { ok: false, reason: "This confirmation was already used." };
  if (Date.now() > pending.expiresAt) return { ok: false, reason: "This confirmation expired — ask again and confirm within 5 minutes." };
  pending.used = true;
  return { ok: true, pending };
}
