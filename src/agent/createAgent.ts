import { readFileSync, writeFileSync, existsSync, mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { elevenlabs } from "../elevenlabsClient.js";
import { env } from "../config.js";
import { SYSTEM_PROMPT } from "./systemPrompt.js";
import { buildToolDefinitions } from "./tools.js";

const STATE_PATH = new URL("../../data/agent-state.json", import.meta.url).pathname;

interface AgentState {
  secretId?: string;
  toolIds?: Record<string, string>;
  agentId?: string;
}

function loadState(): AgentState {
  if (!existsSync(STATE_PATH)) return {};
  return JSON.parse(readFileSync(STATE_PATH, "utf-8"));
}

function saveState(state: AgentState): void {
  mkdirSync(dirname(STATE_PATH), { recursive: true });
  writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}

async function ensureSecret(state: AgentState): Promise<string> {
  if (state.secretId) {
    console.log(`Reusing existing workspace secret ${state.secretId}`);
    return state.secretId;
  }
  const secret = await elevenlabs.conversationalAi.secrets.create({
    name: "tool-webhook-shared-secret",
    value: env.TOOL_WEBHOOK_SECRET,
  });
  console.log(`Created workspace secret ${secret.secretId}`);
  return secret.secretId;
}

async function ensureTools(secretId: string, state: AgentState): Promise<Record<string, string>> {
  const definitions = buildToolDefinitions(secretId);
  const currentNames = new Set(definitions.map((def) => (def.toolConfig as { name: string }).name));
  const toolIds: Record<string, string> = {};

  for (const def of definitions) {
    const name = (def.toolConfig as { name: string }).name;
    const existingId = state.toolIds?.[name];
    if (existingId) {
      await elevenlabs.conversationalAi.tools.update(existingId, def);
      toolIds[name] = existingId;
      console.log(`Updated tool ${name} (${existingId})`);
      continue;
    }
    const created = await elevenlabs.conversationalAi.tools.create(def);
    toolIds[name] = created.id;
    console.log(`Created tool ${name} (${created.id})`);
  }

  // Force-delete tools from a previous run that are no longer defined (e.g.
  // renamed/replaced), so the workspace doesn't accumulate orphans.
  for (const [name, id] of Object.entries(state.toolIds ?? {})) {
    if (!currentNames.has(name)) {
      await elevenlabs.conversationalAi.tools.delete(id, { force: true });
      console.log(`Deleted retired tool ${name} (${id})`);
    }
  }

  return toolIds;
}

async function ensureAgent(toolIds: Record<string, string>, state: AgentState): Promise<string> {
  const conversationConfig = {
    agent: {
      firstMessage: "Hi, it's your assistant. How can I help today?",
      language: "en",
      prompt: {
        prompt: SYSTEM_PROMPT,
        toolIds: Object.values(toolIds),
      },
    },
    turn: {
      // Tuned for slower speech and long pauses rather than snappy turn-taking.
      turnEagerness: "patient" as const,
      turnTimeout: 12,
      silenceEndCallTimeout: 300,
      mergeWithDefaultIgnoreTerms: true,
    },
  };

  if (state.agentId) {
    try {
      await elevenlabs.conversationalAi.agents.update(state.agentId, { conversationConfig });
      console.log(`Updated agent ${state.agentId}`);
      return state.agentId;
    } catch (err) {
      const statusCode = (err as { statusCode?: number }).statusCode;
      if (statusCode !== 404) throw err;
      console.log(`Cached agent ${state.agentId} no longer exists on ElevenLabs — creating a new one.`);
    }
  }

  const created = await elevenlabs.conversationalAi.agents.create({
    name: "Companion Voice Agent",
    conversationConfig,
  });
  console.log(`Created agent ${created.agentId}`);
  return created.agentId;
}

async function main() {
  console.log(`Using webhook base URL: ${env.WEBHOOK_BASE_URL}`);
  console.log("Make sure this URL is publicly reachable (e.g. via ngrok) before agent calls come in.\n");

  const state = loadState();

  const secretId = await ensureSecret(state);
  saveState({ ...state, secretId });

  const toolIds = await ensureTools(secretId, { ...state, secretId });
  saveState({ ...state, secretId, toolIds });

  const agentId = await ensureAgent(toolIds, { ...state, secretId, toolIds });
  saveState({ secretId, toolIds, agentId });

  console.log(`\nDone. Agent ID: ${agentId}`);
  console.log("Register the post-call webhook URL in the ElevenLabs dashboard (Conversational AI > Settings > Webhooks):");
  console.log(`  ${env.WEBHOOK_BASE_URL.replace(/\/$/, "")}/webhooks/post-call`);
  console.log("Copy the signing secret it shows you into POST_CALL_WEBHOOK_SECRET in .env");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
