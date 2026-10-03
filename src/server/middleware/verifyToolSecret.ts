import { timingSafeEqual } from "node:crypto";
import type { NextFunction, Request, Response } from "express";
import { env } from "../../config.js";

/** Server tools authenticate with a static shared-secret header (configured as a
 * workspace secret reference on each tool, see agent/tools.ts) rather than an
 * HMAC signature — that's how ElevenLabs tool-call webhooks work. Post-call
 * webhooks are signed instead; see routes/postCall.ts. */
export function verifyToolSecret(req: Request, res: Response, next: NextFunction): void {
  const header = req.header("x-tool-webhook-secret") ?? "";
  const expected = env.TOOL_WEBHOOK_SECRET;

  const headerBuf = Buffer.from(header);
  const expectedBuf = Buffer.from(expected);
  const matches = headerBuf.length === expectedBuf.length && timingSafeEqual(headerBuf, expectedBuf);

  if (!matches) {
    res.status(401).json({ error: "unauthorized" });
    return;
  }
  next();
}
