# EWOK — OpenClaw-style harness on Pydantic AI

A small local AI harness inspired by [OpenClaw](https://docs.openclaw.ai/architecture):
a long-lived **WebSocket gateway**, session-serialized agent turns, streamed
`lifecycle` / `assistant` / `tool` events, plus a CLI that can talk to the
gateway or run **embedded** turns (`agent exec`) without one.

The agent loop is [Pydantic AI](https://ai.pydantic.dev/) with
[Pydantic AI Harness](https://pydantic.dev/docs/ai/harness/overview/) capabilities
(`LocalWorkspace`, `Coder`, native `WebSearch` when supported, plus always-on DuckDuckGo `web_search`
and a host `computer` tool for screenshots + pointer/keyboard control).

This is **not** a full OpenClaw clone (no messaging channels, device identity,
or protocol v4). It keeps a compatible *feel*: `connect` handshake, `req`/`res`/`event`
frames, immediate `agent` accept with `runId`, and `agent.wait`.

### Memory

Bootstrap + durable memory live under `CLAW_STATE_DIR/workspace` (default
`.claw/workspace`), separate from the coding `CLAW_WORKSPACE`:

- `AGENTS.md` / `SOUL.md` / `IDENTITY.md` / `USER.md` — persona (injected each turn)
- `MEMORY.md` — curated long-term memory (injected for private sessions)
- `memory/YYYY-MM-DD.md` — daily notes (on-demand via tools)

Tools: `memory_get`, `memory_search`, `memory_append`. Ask EWOK to “remember …” and it
should write durable facts to disk (not only chat history).

## Install

Requires Python 3.11+.

```bash
# with uv (recommended)
uv sync --extra dev

# or pip
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set keys as needed.

## Environment

| Variable | Default | Meaning |
|----------|---------|---------|
| `CLAW_MODEL` | `openai:gpt-4.1` | Any `provider:model` string (`azure:…`, `openai:…`, `anthropic:…`), or `test` offline |
| `AZURE_OPENAI_ENDPOINT` | — | Foundry OpenAI v1 URL ending in `/openai/v1/` |
| `AZURE_OPENAI_API_KEY` | — | Azure / Foundry API key |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | — | Provider keys when not using Azure |
| `CLAW_WORKSPACE` | `.` | Root for file/shell tools |
| `CLAW_STATE_DIR` | `.claw` | Sessions, agent home (`workspace/`), local state |
| `GATEWAY_HOST` / `GATEWAY_PORT` | `127.0.0.1` / `18789` | Gateway bind |
| `GATEWAY_TOKEN` | unset | Optional shared secret (`connect.params.auth.token`) |
| `CLAW_QUEUE_MODE` | `followup` | `steer` \| `followup` \| `collect` \| `interrupt` |
| `CLAW_HEARTBEAT` | `0m` (off) | Ambient check-in interval (`30m`, `1h`, or `off`) |
| `CLAW_HEARTBEAT_SESSION` | `main` | Session key for heartbeat turns |
| `CLAW_AGENT_ID` | `main` | Agent id used in OpenClaw-shaped session keys |
| `CLAW_DM_SCOPE` | `main` | `main` \| `per-peer` \| `per-channel-peer` \| `per-account-channel-peer` |
| `CLAW_GROUP_SCOPE` | `per-group` | `per-group` \| `main` |
| `CLAW_IDENTITY_LINKS` | unset | JSON map of canonical peer → channel aliases |
| `CLAW_SPACETIME_URI` | unset | SpacetimeDB HTTP base (e.g. `http://127.0.0.1:3001`) |
| `CLAW_SPACETIME_DB` | unset | Database name (e.g. `claw-jobs`) — enables multi-worker job claims |
| `CLAW_SPACETIME_TOKEN` | unset | Optional Bearer token for SpacetimeDB |
| `CLAW_SPACETIME_WORKER_ID` | `claw-<pid>` | Worker id used when claiming jobs |
| `CLAW_COMPUTER_USE` | `1` | Host desktop control via `computer` tool (`0`/`false`/`off` disables) |

For Microsoft Foundry, prefer `CLAW_MODEL=azure:<deployment-name>` with
`AZURE_OPENAI_ENDPOINT=https://<resource>.services.ai.azure.com/openai/v1/`.
Use the **deployment name** from the project (not the catalog model id).
The project URL (`/api/projects/...`) is for management APIs; chat uses `/openai/v1/`.
Reasoning models that use tools (e.g. `gpt-6-astra`) need
`CLAW_MODEL=azure-responses:<deployment-name>` — chat completions rejects
function tools with `reasoning_effort`.
Native `WebSearch` is disabled for `azure:` / `azure-responses:` models; use the DuckDuckGo `web_search` tool instead (always registered).

**Safety:** `Coder` includes unrestricted shell on the host workspace (same as
Pydantic AI Harness defaults). `computer` can move the real mouse/keyboard and
capture the screen on the gateway host — disable with `CLAW_COMPUTER_USE=0`.
On macOS, grant **Accessibility** and **Screen Recording** to your terminal or
Python runtime. The gateway binds to localhost by default.

## Usage

```bash
# Interactive TUI (embedded agent, no gateway)
claw tui
claw tui -s demo
# Drag to select, Ctrl+C copy selection, Ctrl+Shift+C last reply, Ctrl+Shift+Y full transcript

# Automations (generic OpenClaw cron — not reminder-specific)
claw gateway   # CronService lives here; keep it running
# Agent tool: automations(action="add", job={schedule, payload, ...})
# CLI (aliases: cron, remind):
claw automations --at 1m -m "follow up on X"
claw automations --every 10m -m "check the queue"
claw automations --cron "0 9 * * *" -m "morning brief"
claw automations list
claw cron watch   # headless executor if gateway is not up

# Optional: SpacetimeDB job coordination (atomic claim across gateways)
# spacetime start --listen-addr 127.0.0.1:3001
# cd spacetimedb && spacetime publish claw-jobs -p spacetimedb -s local3001 -y
# CLAW_SPACETIME_URI=http://127.0.0.1:3001 CLAW_SPACETIME_DB=claw-jobs claw gateway

# Terminal A — control plane
claw gateway
# Open the web chat: http://127.0.0.1:18789/

# Terminal B — one turn through the gateway
claw agent -m "hello"

# Embedded turn (no gateway; good for CI / scripts)
CLAW_MODEL=test claw agent exec -m "hello"

# Persist conversation under a session key
claw agent exec -m "remember that my favorite color is blue" -s demo
claw agent exec -m "what is my favorite color?" -s demo

# New chat (empty transcript; MEMORY.md stays)
claw tui --new
claw agent new          # prints a session key
# In TUI: /new  or  Ctrl+N

# Wipe chats + MEMORY.md + persona files + cron jobs, re-seed defaults
claw reset --yes
# In TUI: /scratch yes

# Queue modes (OpenClaw-style) + heartbeat
CLAW_QUEUE_MODE=followup claw gateway
CLAW_HEARTBEAT=30m claw gateway          # ambient main-session check-in
# Per turn: claw agent exec -m "…" --queue interrupt
# In TUI: /queue steer|followup|collect|interrupt
```

### Queue modes

| Mode | When a message arrives mid-run |
|------|--------------------------------|
| `followup` (default) | Wait, then run as the next turn |
| `collect` | Coalesce pending messages into one later turn |
| `interrupt` | Cancel the active turn; start the new message |
| `steer` | Cancel + restart with the new message (Pydantic AI has no mid-tool steer) |

Heartbeat defers automatically while that session lane is busy.

### Multi-channel routing

Inbound messages are normalized and routed to OpenClaw-shaped session keys:

| Source | Default sessionKey |
|--------|--------------------|
| Your DMs (`CLAW_DM_SCOPE=main`) | `agent:main:main` |
| Group | `agent:main:<channel>:group:<id>` |
| Channel/room | `agent:main:<channel>:channel:<id>` |

```bash
# Preview the key a channel peer would get
claw channel key -c telegram --peer-id 111 --peer-kind direct

# Simulate inbound (routes + runs)
claw channel inbound -c slack --peer-id C123 --peer-kind channel -m "hello"

# HTTP ingress for real adapters (Telegram/Slack bots POST here)
curl -s localhost:18789/v1/channel/inbound -H 'content-type: application/json' -d '{
  "channel":"telegram","message":"hi",
  "peer":{"kind":"direct","id":"42"}
}'
```

Gateway WS also supports `channel.inbound` (accepts immediately; use `agent.wait`).
Each turn gets a **Delivery** block (channel / replyTo / thread) so the model knows
which conversation it is in. `MEMORY.md` stays private to DMs/main — not groups.


Gateway WebSocket URL: `ws://127.0.0.1:18789/ws`

### Wire protocol (subset)

1. Client connects; **first frame must be** `connect`.
2. Frames: `{type:"req", id, method, params}` → `{type:"res", id, ok, payload|error}`
3. Server pushes `{type:"event", event:"agent"|"heartbeat"|"cron", payload, seq}`.
   Agent streams use `stream` ∈ `lifecycle` | `assistant` | `tool`.
4. Methods: `connect`, `health`, `agent` (accepts optional `queueMode`; returns
   `{runId, acceptedAt, sessionKey, queueMode, queued}`), `agent.wait`, plus `cron.*`.

## Develop

```bash
uv run pytest
CLAW_MODEL=test uv run claw agent exec -m "ping"
```

## Layout

```
src/claw/
  agent.py         # Pydantic AI agent factory
  computer_use.py  # host screenshot + pointer/keyboard tool
  runner.py        # session lanes, streaming, waiters
  sessions.py      # disk-backed message history
  events.py        # OpenClaw-style event mapping
  gateway/         # FastAPI WebSocket control plane
  cli.py           # typer entrypoints
  tui.py           # Textual chat UI
```

---

# ElevenLabs voice companion agent

A separate Node/TypeScript project living alongside `src/claw/` — a voice-first
companion agent for older adults, built on ElevenLabs Conversational AI. See
`reqs.md` for the requirements and architecture.

## Setup

```bash
npm install
cp .env.example .env   # fill in ELEVENLABS_API_KEY and TOOL_WEBHOOK_SECRET
```

`ELEVENLABS_API_KEY` needs **Conversational AI: Read + Write** for this Node
project. If claw's voice channel (below) also uses this key, it additionally
needs **Speech to Text** and **Text to Speech** — nothing else (not Voices,
Workspace members, etc.).

## Run

```bash
npm run dev          # Express server: tool webhooks + post-call webhook
ngrok http 3000       # or: cloudflared tunnel --url http://localhost:3000
```

Set `WEBHOOK_BASE_URL` in `.env` to the tunnel's `https://` URL, register
`<WEBHOOK_BASE_URL>/webhooks/post-call` in the ElevenLabs dashboard
(Conversational AI → Settings → Webhooks) and copy its signing secret into
`POST_CALL_WEBHOOK_SECRET`. Then:

```bash
npm run create-agent  # registers tools + creates/updates the agent
```

Re-running `create-agent` is safe — state is cached in `data/agent-state.json`
(gitignored) so it updates in place.

## Layout

- `src/agent/` — system prompt, tool definitions, the script that registers them with ElevenLabs.
- `src/policy/` — tiered permissions, spending caps, payee allowlist/cooling-off, confirmation tokens. Enforced server-side, independent of the model.
- `src/memory/` — JSON-file-backed memory store (`data/memory.json`), inspectable/editable outside the agent.
- `src/scam/` — rules-based scam-pattern classifier for untrusted inbound content.
- `src/notify/` — rate-limited trusted-contact notification stub.
- `src/server/` — Express app wiring the tool and webhook routes.

## claw's voice channel (browser prototype)

Separately, `src/claw/channels/voice.py` lets **claw itself** (its own
Pydantic AI agent, memory, tools) speak and listen, using ElevenLabs' raw
Speech-to-Text / Text-to-Speech endpoints — not the Conversational AI Agents
platform above. It plugs into claw's existing multi-channel routing
(`claw/channels/dispatch.py`) exactly like Telegram/Slack/CLI do, just with
audio instead of typed text, so memory/tools/sessions behave identically.

```bash
uv run claw gateway   # or: source .venv/bin/activate && python -m claw.gateway...
# open http://127.0.0.1:18789/voice, hold the button, speak, release
```

Requires `ELEVENLABS_API_KEY` (see permission note above) and optionally
`ELEVENLABS_VOICE_ID` in `.env`. This is a push-to-talk prototype (no
server-side VAD, no telephony) — see `reqs.md` for the requirements this is
working toward and the plan at the time of writing for what's deliberately
deferred (streaming STT/TTS for lower latency, Twilio phone calls).
