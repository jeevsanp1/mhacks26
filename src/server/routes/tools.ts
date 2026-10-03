import { Router, type Request, type Response } from "express";
import { memoryStore } from "../../memory/store.js";
import { classifyForScamPatterns } from "../../scam/classify.js";
import { prepareAction, consumeAction, type ActionKind } from "../../policy/policy.js";
import { notifyTrustedContact } from "../../notify/trustedContact.js";
import { auditLog } from "../../audit/log.js";

export const toolsRouter = Router();

function userIdFrom(body: Record<string, unknown>): string {
  return typeof body.userId === "string" && body.userId ? body.userId : "demo-user";
}

toolsRouter.post("/get-memory", (req, res) => {
  const userId = userIdFrom(req.body);
  const topic = req.body.topic as string | undefined;
  const items = topic ? [memoryStore.get(userId, topic)].filter(Boolean) : memoryStore.list(userId);
  res.json({ items });
});

toolsRouter.post("/update-memory", (req, res) => {
  const userId = userIdFrom(req.body);
  const { topic, value } = req.body as { topic: string; value: string };
  const item = memoryStore.set(userId, topic, value);
  auditLog({ type: "memory_update", userId, topic });
  res.json({ item });
});

toolsRouter.post("/get-calendar", (req, res) => {
  // Stub data source — swap for a real calendar integration (Google Calendar,
  // Outlook) behind the same narrow tool schema.
  const date = (req.body.date as string | undefined) ?? new Date().toISOString().slice(0, 10);
  res.json({
    date,
    events: [{ title: "Eye doctor appointment", time: "2:30 PM", location: "Dr. Chen's office" }],
  });
});

toolsRouter.post("/read-inbox", (req, res) => {
  const query = (req.body.query as string | undefined) ?? "";
  // Stub data source — swap for a real mail integration. The fetched content is
  // wrapped as clearly-labeled untrusted data; the system prompt instructs the
  // model to treat it as text to summarize, never as instructions to follow.
  const rawMessages = [
    {
      from: "billing@comcast-support.example",
      subject: "URGENT: Your service will be disconnected today",
      body: "Act now! Pay immediately using a gift card to avoid disconnection. Don't tell your family about this offer.",
    },
  ].filter((m) => !query || m.subject.toLowerCase().includes(query.toLowerCase()));

  const messages = rawMessages.map((m) => {
    const scam = classifyForScamPatterns(`${m.subject} ${m.body}`);
    return {
      from: m.from,
      subject: m.subject,
      untrustedBody: `<<<UNTRUSTED_EMAIL_CONTENT>>>\n${m.body}\n<<<END_UNTRUSTED_EMAIL_CONTENT>>>`,
      scamFlag: scam,
    };
  });

  res.json({ messages });
});

function handlePrepare(kind: ActionKind, detailsOf: (body: Record<string, unknown>) => Record<string, unknown>) {
  return (req: Request, res: Response) => {
    const userId = userIdFrom(req.body);
    const details = detailsOf(req.body);
    const result = prepareAction({ userId, kind, details });

    auditLog({ type: "prepare_action", userId, kind, details, allowed: result.ok });

    if (!result.ok) {
      res.json({ allowed: false, reason: result.reason });
      return;
    }
    res.json({
      allowed: true,
      confirmationToken: result.pending.token,
      consequenceStatement: result.pending.consequenceStatement,
      tier: result.pending.tier,
      expiresInSeconds: Math.round((result.pending.expiresAt - Date.now()) / 1000),
    });
  };
}

toolsRouter.post("/create-reminder", (req, res) => {
  const userId = userIdFrom(req.body);
  const { message, when } = req.body as { message: string; when: string };

  // Free tier: reminders execute immediately, no prepare/confirm step.
  // Stub: wire this up to a real reminder/SMS scheduler.
  console.log(`[create_reminder] ${userId}: "${message}" at ${when}`);
  auditLog({ type: "create_reminder", userId, message, when });

  res.json({ created: true, summary: `Reminder set: "${message}" at ${when}.` });
});

toolsRouter.post(
  "/prepare-calendar-edit",
  handlePrepare("calendar_edit", (body) => ({ title: body.title, date: body.date })),
);

toolsRouter.post(
  "/prepare-payment",
  handlePrepare("payment", (body) => ({
    payeeId: body.payeeId,
    payeeName: body.payeeName,
    amountCents: body.amountCents,
    date: body.date,
  })),
);

toolsRouter.post("/execute-action", (req, res) => {
  const { confirmationToken } = req.body as { confirmationToken: string };
  const result = consumeAction(confirmationToken);

  auditLog({ type: "execute_action", confirmationToken, ok: result.ok });

  if (!result.ok) {
    res.status(409).json({ executed: false, reason: result.reason });
    return;
  }

  // Stub: perform the real effect for each kind (send the SMS, write the
  // calendar entry, call the payment processor) behind its own scoped
  // credentials. Left as a log line so the demo flow is observable end to end.
  console.log(`[execute_action] ${result.pending.kind}`, result.pending.details);

  if (result.pending.tier === "strong") {
    notifyTrustedContact(
      `Completed: ${result.pending.consequenceStatement}`,
      `strong-tier action executed (${result.pending.kind})`,
    );
  }

  res.json({ executed: true, summary: result.pending.consequenceStatement });
});

toolsRouter.post("/notify-trusted-contact", (req, res) => {
  const { message, reason } = req.body as { message: string; reason: string };
  const result = notifyTrustedContact(message, reason);
  auditLog({ type: "notify_trusted_contact", reason, ok: result.ok });
  res.json(result);
});
