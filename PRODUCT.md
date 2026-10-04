# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
Older adults are the primary audience: people who may have slow speech, hearing aids, accents, long pauses, or limited comfort with technology, and who often won't install an app. The older person is the principal; the agent serves their goals even when a family member sets it up. (Source: reqs.md, src/agent/systemPrompt.ts.)

Open: which user the web dashboard itself addresses (the older adult directly, a family caregiver/trusted contact, or both) is not yet confirmed. The user said only that the work should be "geared towards older people."

## Product Purpose
Claw is a voice-first AI assistant for older adults that simplifies complex technology (email, computer use), automates menial tasks, and keeps the person in control. Reading and reminders are free; money, identity, and sharing personal info get strong, plain-language friction. The project is a hackathon demo (MHacks 26); success is a clear, credible demonstration of this idea.

## Positioning
The policy layer (permission tiers, spending caps, scam checks) lives outside the model, so the agent cannot be talked out of its rules. The older person stays the principal, with settings they can see and change.

## Operating Context
Channels: voice (ElevenLabs, Vapi phone number), web chat, and a local gateway dashboard. Backend is a Python WebSocket gateway (`src/claw`) plus a Node/TypeScript ElevenLabs integration (`src/agent`, `src/policy`, `src/scam`, `src/notify`). The dashboard is served from `src/claw/gateway/static/dashboard.html` and connects to Nessie (mock bank) and FinchNode (synthetic patient data).

## Capabilities and Constraints
- Confirmations state the concrete consequence ("This pays $240 to Comcast from your checking account tomorrow"), not "Approve action?"
- One thing at a time; one simple question when unsure; "stop" and "undo" work instantly.
- Memory (routines, medications, people, preferences) is inspectable, correctable, and deletable by the person.
- Inbound content (emails, texts, pages) is untrusted; scam patterns (urgency, gift cards, wire transfers, "don't tell your family") are flagged.
- Bank and health data are mock or synthetic; the UI labels sample data as "mock" and must never present it as real.
- Gateway binds localhost; auth token optional.

## Brand Commitments
- Working name: "Claw" (current name, not confirmed final).
- Binding visual constraint volunteered by the user: a clean, restrained look in the vein of 2010s Apple design. Recorded as stated; its translation into a visual world belongs to the design phase.
- Design system follows SBB (digital.sbb.ch) in structure and principles, not in visual identity: tokens, components, and patterns, with UX-writing and notification guidelines alongside. SBB's principles: User-centred, Recognisable, Inclusive, Reduced, Holistic, Self-explanatory, Task-oriented.

## Evidence on Hand
- `reqs.md` (requirements and architecture outline), `README.md`, `src/agent/systemPrompt.ts` (voice and behavior).
- Existing dashboard: `src/claw/gateway/static/dashboard.html`.
- No real users, testimonials, or usage data exist; do not fabricate any.

## Product Principles
1. The older person is the principal: visible, changeable settings; no infantilizing defaults.
2. Reduced and self-explanatory: one task at a time, plain language, no jargon.
3. Consequences are stated concretely before anything involving money, identity, or personal info.
4. Trust is inspectable: memory, audit, and scam flags are visible, never hidden.
5. Honest about data: mock and synthetic content is labeled as such.

## Accessibility & Inclusion
Large text is a first-class alternative to voice. Design for slow reading, low vision, hearing loss, and low technical confidence. Specific standard not yet set; WCAG AA is the assumed floor (unconfirmed).
