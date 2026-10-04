import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { env } from "../config.js";
import { vapi } from "../vapiClient.js";

const STATE_PATH = new URL("../../data/vapi-state.json", import.meta.url).pathname;

interface VapiState {
  assistantId?: string;
}

function loadState(): VapiState {
  return existsSync(STATE_PATH) ? JSON.parse(readFileSync(STATE_PATH, "utf-8")) : {};
}

function saveState(state: VapiState): void {
  mkdirSync(dirname(STATE_PATH), { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}

// Vapi handles telephony, speech-to-text and text-to-speech; the "brain" is the
// claw agent, reached through its OpenAI-compatible /vapi/<secret>/chat/completions
// endpoint (see claw/gateway/server.py). Persona, memory and tools all live in
// claw, so the assistant carries no system prompt or tools of its own.
async function main() {
  const clawUrl = `${env.CLAW_PUBLIC_URL.replace(/\/$/, "")}/vapi/${env.VAPI_LLM_SECRET}`;
  console.log(`EWOK gateway URL for Vapi: ${env.CLAW_PUBLIC_URL}/vapi/<secret>`);
  console.log("Make sure it is publicly reachable (e.g. a tunnel to the gateway port) before calls come in.\n");

  const assistant = {
    name: "EWOK Voice Agent",
    firstMessage: "Hi, it's your assistant. How can I help today?",
    model: { provider: "custom-llm", url: clawUrl, model: "claw" },
    // Vapi-provided voice. A custom ElevenLabs voice needs your ElevenLabs key added
    // as a credential in the Vapi dashboard first; override with VAPI_VOICE_ID.
    voice: { provider: "vapi", voiceId: process.env.VAPI_VOICE_ID ?? "Savannah" },
    // Tuned for slower speech and long pauses rather than snappy turn-taking.
    startSpeakingPlan: { waitSeconds: 1.5 },
    silenceTimeoutSeconds: 300,
  };

  const state = loadState();
  let assistantId = state.assistantId;
  if (assistantId) {
    await vapi("PATCH", `/assistant/${assistantId}`, assistant);
    console.log(`Updated assistant ${assistantId}`);
  } else {
    assistantId = (await vapi<{ id: string }>("POST", "/assistant", assistant)).id;
    console.log(`Created assistant ${assistantId}`);
  }
  saveState({ assistantId });
  console.log(`\nDone. Assistant ID: ${assistantId}. Next: npm run import-phone`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
