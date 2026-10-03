import { Router } from "express";
import { elevenlabs } from "../../elevenlabsClient.js";
import { env } from "../../config.js";
import { auditLog } from "../../audit/log.js";

export const postCallRouter = Router();

postCallRouter.post("/post-call", async (req, res) => {
  const rawBody = (req.body as Buffer).toString("utf-8");
  const signature = req.header("elevenlabs-signature") ?? "";

  try {
    const event = await elevenlabs.webhooks.constructEvent(rawBody, signature, env.POST_CALL_WEBHOOK_SECRET);
    auditLog({ type: "post_call_webhook", event });
    res.status(200).json({ received: true });
  } catch (err) {
    auditLog({ type: "post_call_webhook_rejected", error: String(err) });
    res.status(400).json({ error: "invalid signature" });
  }
});
