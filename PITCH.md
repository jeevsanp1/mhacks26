# Claw — Pitch Deck

> One slide per `##` heading. Working name "Claw"; easy to swap.

---

## 1. Title

# Claw
**A voice-first AI assistant that makes technology work for older adults, and keeps them in control.**

MHacks 26

---

## 2. The problem

- Technology keeps getting more complicated: email, banking, appointments, portals.
- Older adults are the most heavily targeted group for scams.
- Most assistants assume fast speech, clean audio, an app install and tech confidence.
- The people who could benefit most are the ones least served.

---

## 3. Our users

Older adults, as the **principal**. The agent serves *their* goals, even when a family member sets it up.

- Slow speech, long pauses, accents
- Hearing aids, low vision
- Low technical confidence
- Won't install an app

---

## 4. The solution

**Talk to Claw like a person. It handles the rest.**

| Channel | How it helps |
|---|---|
| Phone call (Vapi number) | No app, no install. Just dial. |
| Voice in the browser (ElevenLabs STT/TTS) | Push-to-talk, spoken replies |
| Web chat | Large-text alternative to voice |
| Dashboard | Everything Claw knows, in one place the person can read |

---

## 5. Accessibility features

### Speak, don't type
- Voice-first on every channel, including a plain phone call
- English-only speech recognition and synthesis, so accents don't drift
- Streaming speech-to-text and text-to-speech for low-latency replies

### Patient by design
- Spoken fillers ("One moment.", "Just a sec.") when the agent is working, so silence never feels like failure
- Turn-taking tuned for slow speech and long pauses
- Agent is instructed to repeat, slow down, and never assume jargon

### One thing at a time
- One simple question when unsure, never five options
- "Stop" and "undo" work instantly

### Large text and a readable dashboard
- Large-text web chat is a first-class alternative to voice
- Dashboard built for the older adult directly, not a caregiver console
- Greeting ("hiya, rishi") and a clock widget for orientation
- Design target: WCAG 2.2 AAA (7:1 contrast, enhanced target sizes)

### Memory that restates context
- Remembers routines, medications, people, preferences
- Gently re-orients: "Last time we were scheduling your eye appointment..."
- Inspectable, correctable, deletable by the person

---

## 6. Safety as an accessibility feature

Trust is what makes people willing to use it.

**Tiered permissions, in plain language**
- Reading and reminders: free
- Reversible, low-stakes actions: light confirmation
- Money, identity, personal info: strong friction

**Concrete confirmations**
> "This pays $240 to Comcast from your checking account tomorrow."

Not "Approve action?"

**Rules the model can't talk its way around**
- Policy layer lives *outside* the model, in our backend
- $500 per-payment cap and $1,000 daily cap
- Payee allowlist plus a 24-hour cooling-off for new payees
- Single-use confirmation tokens
- No tool exists for sharing passwords or SSNs: safe by absence of capability

**Scam protection**
- All inbound email, text and web content treated as untrusted
- Flags urgency, gift cards, wire transfers, "don't tell your family", credential requests and impersonation
- Agent says it plainly: "This looks like it could be a scam. Want me to read it, or flag it to your family?"
- "Forward me anything suspicious" becomes a habit

**Oversight without taking control**
- Rate-limited trusted-contact notifications
- Full transcript and tool-call audit log
- Autonomy settings the person can see and change

---

## 7. Connectors dashboard

Ask in chat or by voice, and the matching panel opens next to the conversation.

| Connector | Shows | Voice/chat trigger |
|---|---|---|
| Nessie | Accounts, balances, recent purchases | "nessie" |
| FinchNode Health | Conditions, medications, labs, vitals, appointments | "health" |
| Calendar | Upcoming events | "calendar" |
| **+** | More connectors can be added | Suggest one |

Claw can combine them: "How much do my medications cost me?" pulls from health and banking together.

---

## 8. Use cases

**Medication management**
"What medications am I on, and when's my next refill?" The answer comes from health records, with a reminder set by voice.

**Understanding my money**
"Did I pay for Netflix this month?" It reads recent purchases in plain English and never moves money without a consequence statement.

**Appointments and schedule**
"What's on my calendar this week?" Read aloud, with reminders for what's coming up.

**Scam second opinion**
Forward a suspicious email or text. Claw flags the red flags, explains why, and offers to notify family.

**Simplifying email and the computer**
"Open my email." Claw operates the desktop and browser for them, so they don't have to hunt for icons.

**Reminders and routines**
"Remind me to take my pills at 8." Scheduled automations run on their own, including outbound reminders.

**Family peace of mind**
A trusted contact is notified for scam flags or after high-stakes actions, without taking control away.

---

## 9. How it works

| Layer | What it does |
|---|---|
| Interface | Vapi phone, ElevenLabs voice, web chat, dashboard |
| Agent | Python gateway on Pydantic AI, with session memory and tools |
| Policy | Permission tiers, caps, allowlists, outside the model |
| Tools | Narrow, single-purpose, scoped credentials |
| Memory | Inspectable store the person owns |
| Oversight | Audit log and trusted-contact notifications |

---

## 10. Honest about where we are

- Voice, chat, dashboard, connectors, memory, scam flagging and the policy layer are built.
- Bank and health data come from mock and synthetic sources (Nessie, FinchNode).
- Still to do: real payment processing, SMS channel, a dedicated large-text companion view.
- No real-user testing yet, so no usage claims.

---

## 11. The ask

**Technology should adapt to people, not the other way around.**

Claw gives older adults a patient voice, a clear screen, and guardrails that keep them in charge.
