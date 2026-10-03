import express from "express";
import { env } from "../config.js";
import { toolsRouter } from "./routes/tools.js";
import { postCallRouter } from "./routes/postCall.js";
import { verifyToolSecret } from "./middleware/verifyToolSecret.js";

const app = express();

// Mounted before the global JSON parser so the handler gets the exact raw
// bytes ElevenLabs signed — required for HMAC verification to succeed.
app.use("/webhooks", express.raw({ type: "application/json" }), postCallRouter);

app.use(express.json());
app.use("/tools", verifyToolSecret, toolsRouter);

app.get("/health", (_req, res) => res.json({ ok: true }));

app.listen(env.PORT, () => {
  console.log(`Server tool + webhook endpoint listening on http://localhost:${env.PORT}`);
  console.log(`Public base URL configured as ${env.WEBHOOK_BASE_URL} (update via tunnel for local dev)`);
});
