The person stays the principal. The older person is the one whose goals the agent serves, even when a family member sets it up. That means no infantilizing defaults, settings the person can see and change, and autonomy levels that can be adjusted up or down as their needs change.
Interface: meet people where they already are. Voice-first, with large text as the alternative. Phone calls or SMS as channels, since many people won't install an app. The agent should be patient with repetition, slow speech, hearing aids, accents, and long pauses, and never assume jargon. It should also do one thing at a time, ask one simple question when unsure instead of presenting five options, and let "stop" or "undo" work instantly.
Simplify already complex technology. Tools to help simplify things like e-mail and computer use to simplify complicated tasks
Automate menial tasks.
Tiered permissions, with plain-language confirmations. Reading and reminders are free. Reversible low-stakes actions need light confirmation. Anything involving money, identity, or sharing personal info gets strong friction: spending caps, allowlisted payees, a cooling-off period for new recipients, and a confirmation that states the consequence concretely ("This pays $240 to Comcast from your checking account tomorrow") rather than "Approve action?" Confirmation fatigue is real, so reserve the heavy prompts for the things that matter.
Assume an adversarial environment. Older adults are heavily targeted by scams, and the agent will read emails, texts, and web pages that may be malicious. So the harness should treat all inbound content as untrusted, including instructions embedded in it (prompt injection is just a scam aimed at the agent). It should never hand over passwords or SSNs, and should flag classic patterns like urgency, gift cards, wire transfers, and "don't tell your family." A good feature is a "forward me anything suspicious" habit, where the agent becomes a trusted second opinion.
Memory that is useful and inspectable. Persist routines, medications, important people, and preferences, so the person doesn't have to re-explain things, and let them view, correct, and delete what's stored. Because memory can fade for the user, the agent should gently restate context ("Last time we were scheduling your eye appointment...") instead of assuming shared recall.

Architecturally, I'd split it into an interface layer (voice/SMS/large UI), a policy layer (permission tiers, spending limits, scam checks), a tool layer with narrowly scoped credentials, an inspectable memory store, and an oversight layer for trusted contacts and audit logs. The policy layer should sit outside the model, so it can't be talked out of its rules.
What use case do you have in mind, such as errands and bills, health management, or companionship? The right defaults shift quite a bit between them.

## Implementation Outline: ElevenLabs Conversational AI Agents

ElevenLabs Agents (Conversational AI) is a strong fit for the **interface layer** from the architecture above — it is a hosted voice agent platform with telephony, ASR/TTS tuned for natural turn-taking, and a tool-calling model. It should NOT own the policy layer: per requirement 9, policy must live outside the model so it can't be reasoned away. The design below keeps ElevenLabs as the conversational front door while every sensitive decision is enforced by our own backend.

### 1. Mapping to the architecture

| Layer | Owner | ElevenLabs' role |
|---|---|---|
| Interface (voice/SMS/large UI) | ElevenLabs Agent + Twilio | Primary — handles calls, speech, turn-taking |
| Policy (tiers, limits, scam checks) | Our backend | None — agent only calls server tools; backend decides allow/deny |
| Tools (narrowly scoped credentials) | Our backend (Server Tools / webhooks) | Agent can only invoke allowlisted tool names, never holds credentials |
| Memory (inspectable, editable) | Our backend (DB) | Agent reads/writes via tool calls + dynamic variables; never the source of truth |
| Oversight (trusted contacts, audit log) | Our backend | Agent triggers notification tools; all calls logged via post-call webhook |

### 2. Telephony & channels
- Provision a phone number via Twilio (or native ElevenLabs telephony) and connect it as the agent's inbound/outbound number — no app install required (req 2).
- Outbound calls (e.g., medication reminders) triggered by our backend via the Agents Platform API/SIP trunk, not by the user.
- SMS channel: either ElevenLabs' native text support or a Twilio Messaging webhook that routes to a lightweight text version of the same agent logic, for users who prefer texting over calls.
- Large-text UI is a separate thin web view that reads the same conversation/memory store (read-only "what did we talk about" view) — not served by ElevenLabs itself.

### 3. Conversation design for the target users (req 2)
- System prompt enforces: one topic/question at a time, no jargon, plain confirmations, explicit permission to repeat/slow down.
- Turn-taking/VAD settings tuned for long pauses and slower speech (increase end-of-turn silence threshold; disable aggressive interruption).
- "Stop" / "undo" are modeled as **client tools** wired to an interrupt handler so they fire instantly mid-turn rather than waiting for the agent to finish a sentence.
- Agent restates context at the start of a resumed task ("Last time we were scheduling your eye appointment...") by pulling a memory-summary tool result into context at session start, never assuming recall.

### 4. Tool layer (narrowly scoped credentials)
- Model as ElevenLabs **Server Tools**: each is a single-purpose webhook (e.g. `get_calendar`, `draft_email_reply`, `initiate_payment`, `send_reminder_sms`, `notify_trusted_contact`).
- Tools never embed real credentials in the agent config; the webhook backend holds scoped credentials per integration (email, bank, calendar) and performs the actual third-party call.
- Each tool's JSON schema is intentionally narrow (e.g. `initiate_payment(payee_id, amount, date)` — payee must already be allowlisted; the tool cannot accept arbitrary account numbers).

### 5. Policy enforcement outside the model (req 5)
- Every tool call from the agent passes through a policy middleware in our backend before anything executes:
  - **Free tier** (reading, reminders): executes immediately, no confirmation tool needed.
  - **Light confirmation** (reversible, low-stakes): agent must call a `confirm_light(summary)` tool and get a verbal "yes" captured as a transcript flag before the real action tool fires.
  - **Strong friction** (money/identity/sharing personal info): backend enforces spending caps and payee allowlists independent of what the agent "decides"; agent must speak a concrete consequence string ("This pays $240 to Comcast from checking tomorrow") generated from backend data (not invented by the LLM), get explicit confirmation, and new payees get a cooling-off period enforced server-side regardless of what the conversation says.
- Because the LLM only ever sees tool *results*, a prompt-injected instruction inside a tool result cannot itself raise its own privilege tier — the tier is attached to the tool definition server-side, not inferred by the model.

### 6. Treating inbound content as untrusted (req 6)
- When the agent reads emails/texts/web pages (via a `read_email` or `fetch_page` tool), the tool wraps returned content in a clearly delimited "untrusted data" block in the tool response, and the system prompt explicitly instructs the model to treat anything inside it as data to summarize, never as instructions to follow.
- A lightweight scam-classifier step (either a second cheap LLM call or a rules pass in the tool webhook itself) runs over fetched content before it reaches the conversation, flagging urgency language, gift-card/wire-transfer asks, and "don't tell your family" patterns; flags are surfaced to the user in-call ("this looks like it could be a scam — want me to read it anyway or just flag it to your family?") and logged for oversight.
- Agent is instructed (and the `share_personal_info` tool is simply never defined) so it structurally cannot hand over passwords/SSNs — this is enforced by absence of a capability, not a prompt instruction alone.

### 7. Memory (req 7)
- Source of truth is our own DB (routines, medications, important people, preferences), not ElevenLabs' built-in memory/knowledge base — this is what makes it user-viewable/editable/deletable outside the call.
- Agent accesses it only through `get_memory(topic)` / `update_memory(topic, value)` tools; dynamic variables inject a short relevant-memory summary at session start for context-restating.
- A companion view (web or sent via SMS link) lets the person or a trusted family member page through, correct, or delete stored items directly against the DB — independent of the voice agent.

### 8. Oversight layer (req 9)
- Post-call webhook persists full transcript + tool-call log for every session (audit log).
- `notify_trusted_contact` tool (rate-limited, backend-enforced) is the only path to reach family members, used for scam flags or after strong-friction confirmations complete.
- Autonomy level (how much the agent can do without confirmation) is a per-user backend setting the person can see and change — exposed as plain-language toggles in the companion UI, not buried in the agent prompt.

### 9. Build order
1. Stand up one ElevenLabs agent + Twilio number, minimal system prompt, no tools — validate voice quality/latency/turn-taking for slow speech and accents.
2. Add read-only tools (`get_calendar`, `get_memory`, `read_email` with untrusted-content wrapping) — validate scam-flagging and context-restating behavior.
3. Add light-confirmation action tools (reminders, calendar edits) with the policy middleware in place.
4. Add strong-friction tools (payments) last, with spending caps/allowlists/cooling-off enforced server-side and tested adversarially (attempt prompt-injection from a fake "email" to see if it can trick the agent into skipping confirmation).
5. Add SMS channel and large-text companion view reusing the same backend/tool layer.

### 10. Implementation status

Steps 1–3 above are scaffolded (Node/TypeScript, see README.md for setup):
- `src/server` — Express server exposing the server-tool webhooks and the HMAC-verified post-call webhook.
- `src/policy/policy.ts` — tiered enforcement (free/light/strong), payment caps, payee allowlist + cooling-off, single-use confirmation tokens with a backend-generated consequence statement. Smoke-tested: non-allowlisted payee is rejected, a light-tier reminder issues a token that executes once and is rejected on reuse.
- `src/memory/store.ts` — file-backed memory (`data/memory.json`), inspectable/editable outside the agent.
- `src/scam/classify.ts` — rules-based scam-pattern flagging (urgency, gift cards, wire transfers, secrecy, credential requests), applied to inbound inbox content before it reaches the model.
- `src/agent/` — system prompt + tool definitions + `createAgent.ts` script that registers the tools and agent against the real ElevenLabs API (verified against the `@elevenlabs/elevenlabs-js` SDK's actual request/response types).
- Not yet built: step 4 (payment execution against a real processor), telephony/SMS wiring (Twilio number + routing), and the large-text companion view.

