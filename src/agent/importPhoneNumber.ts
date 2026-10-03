import { readFileSync, existsSync } from "node:fs";
import { vapi } from "../vapiClient.js";

const STATE_PATH = new URL("../../data/vapi-state.json", import.meta.url).pathname;

interface VapiNumber {
  id: string;
  number?: string;
  assistantId?: string;
}

function loadAssistantId(): string {
  if (!existsSync(STATE_PATH)) {
    throw new Error("No data/vapi-state.json found — run `npm run create-vapi-assistant` first.");
  }
  const { assistantId } = JSON.parse(readFileSync(STATE_PATH, "utf-8")) as { assistantId?: string };
  if (!assistantId) throw new Error("vapi-state.json has no assistantId — run `npm run create-vapi-assistant` first.");
  return assistantId;
}

// Gives the assistant a Vapi-provisioned US number. Safe to re-run: an existing
// Vapi number is re-assigned to the current assistant instead of buying another.
async function main() {
  const assistantId = loadAssistantId();
  const name = "Companion Voice Agent";

  const existing = (await vapi<(VapiNumber & { provider?: string; name?: string })[]>("GET", "/phone-number")).find(
    (p) => p.provider === "vapi" && p.name === name,
  );

  let number: VapiNumber;
  if (existing) {
    number = await vapi<VapiNumber>("PATCH", `/phone-number/${existing.id}`, { assistantId });
    console.log(`Re-assigned ${number.number ?? existing.id} to assistant ${assistantId}`);
  } else {
    number = await vapi<VapiNumber>("POST", "/phone-number", {
      provider: "vapi",
      name,
      assistantId,
      ...(process.env.VAPI_AREA_CODE ? { numberDesiredAreaCode: process.env.VAPI_AREA_CODE } : {}),
    });
    console.log(`Created ${number.number ?? number.id} → assistant ${assistantId}`);
  }

  // The number can take a moment to activate; re-fetch it if it isn't populated yet.
  if (!number.number) {
    number = await vapi<VapiNumber>("GET", `/phone-number/${number.id}`);
  }
  console.log(`\nCall ${number.number ?? "(number still activating — check the Vapi dashboard)"} to talk to the assistant.`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
