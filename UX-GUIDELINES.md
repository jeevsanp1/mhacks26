# UX guidelines

Source: the SBB design system (digital.sbb.ch), crawled 2026-10-03: principles, accessibility guide (all seven roles), UX writing, Lyne component rules, AI design basics. Where SBB and an earlier Claw decision disagree, SBB wins. Where WCAG 2.2 AAA is stricter than SBB's AA, AAA wins (PRODUCT.md). Visual rules live in `DESIGN.md`.

## Principles (SBB) and what each means here

| Principle | In Claw |
|---|---|
| User-centred | Test with real older adults, in their own setting, early and often. |
| Recognisable | Same patterns on chat, voice and phone: same words, same confirmation shape. |
| Inclusive | Works with impairments that are permanent, temporary or situational (bright sun, one hand, hearing aids). |
| Reduced | Show as little as possible and as much as necessary. Details sit one level down. |
| Holistic | Design the whole chain: call, text, dashboard and the family member's handoff. |
| Self-explanatory | Use the person's words. Show what is expected and offer simple error correction. Let them adjust preferences. |
| Task-oriented | Start from the task (pay a bill, remember a medicine), not the feature. |
| Appropriate | Automation must not turn people into bored monitors. |

## AI behavior (SBB "AI Design: Basics")
- Higher risk or uncertainty means the person decides and the agent recommends. Low-risk, well-defined tasks may be automated. Start with recommending and automate slowly as trust is earned.
- The person can influence or override any recommendation, and can see why it was made.
- Models make mistakes. Money, identity and sharing always get a human confirmation, enforced outside the model.
- Label AI output as AI.

## Accessibility (SBB guide, held to AAA)

**Visual**
- Most important information is identifiable at first glance; simple structure, clear hierarchy, sensible reading order. Works at 200% zoom.
- Text contrast 7:1 (AAA). Non-text and control edges 3:1 or more. Avoid background images.
- Every touch target at least 44 x 44px. Text at least 16px (this product's body is 18px), sans serif.
- Keyboard focus always obvious (3px ring).

**Interaction**
- Everything works with a keyboard. No reliance on mouse, hover or gestures. Logical focus order.
- Labels and instructions always visible. Say what is wrong and how to fix it. Warn before important actions.
- Never use colour alone: underline links, write the status word.
- Provide an alternative when something cannot be made accessible (for example, text for anything a map or chart shows).
- Avoid unwanted automatic actions and distracting elements.

**Content**
- Prioritise content so it can be scanned. Clear language, no jargon or idioms.
- Images need meaningful alt text; decorative images get empty alt; video needs subtitles; audio needs a transcript or visual equivalent.
- Links make sense on their own. "Continue to sign-in", not a bare "Continue".

**Development**
- Semantic elements, correct language attribute, native controls over custom ones.
- DOM order matches visual order (test with CSS off).
- Skip link plus landmarks (`header`, `main`, `aside`, labelled `section`).
- Dynamic updates in a live region (`role="log"`, `role="status"`).

**Testing**: automated pass (WAVE), headings structure, keyboard-only run, screen reader (VoiceOver, NVDA), 200% zoom, then real users including people with disabilities.

## UX writing (SBB)

**General**: clear; as short as possible and as long as necessary; helpful and reassuring (no irony or filler); consistent terms; context-aware; accessible (labels and ARIA read well aloud). Use internal terms only if the audience already knows them.

**Sentence case**, always. Only product names are capitalised.

**Buttons**: an instruction or a clear destination, 1 to 4 words, no explanation inside the button (put that above or below).
- Good: Save, Send, Go to settings, Show message. Avoid: Yes, Click here, Resend profile invitation.

**Links**: descriptive without surrounding text. "View terms and conditions", not "Click here" or "More".

**Forms**: precise labels that name the field. Placeholders show an example or format ("Date of birth (DD.MM.YYYY)") and never replace the label. Put extra explanation in an info popover or hint.

**Errors**: say what to do. "Enter a valid time (HH:MM)", not "Invalid value". No full stop on a field error unless it is a full sentence. After submitting, say when and how the reply comes.

**Confirmations**: use a dialog for serious or irreversible actions with the consequence stated ("Delete entry? This action cannot be undone."). Offer Undo for reversible changes, for example in a toast.

**Progress**: label steps with nouns ("Subscription"), mark the final step as the end, say what can still be changed later.

**Delete vs deactivate**: they mean different things (permanent vs restorable). Pick the right word and keep it consistent.

## Components: usage rules (from Lyne)
- **Button**: clearly labelled single action; avoid multiple primary buttons per page.
- **Action group**: 2 to 3 actions; one primary; continue on the right, back/cancel on the left.
- **Form field**: always wrap inputs in a labelled field; no bare inputs.
- **Notification**: short and concise; closable or timed; consistent position; not for form errors.
- **Alert**: critical, urgent only; not combined with notifications; not for form errors.
- **Toast**: one at a time, short-lived; no important actions inside (hard to reach) and offer another route; polite live region.
- **Message**: calm, in-layout feedback, such as an empty list or an error page.
- **Dialog**: one task or one piece of information; closable by button, Esc and backdrop; never a trap.
- **Link**: descriptive; external resources open in a new tab.
- **Title**: correct semantic levels; visual size can differ from the level; avoid too many titles.

## Brand and licensing (SBB "Rights of use")
- SBB's logo, signet, icons, pictograms, timetable icons, clock and typeface are restricted to SBB and Swiss public transport; they may not be changed or reused. Claw uses none of them.
- Claw's colours are the user's own palette (teal, coral, mist, ink, tan), not SBB's. Coral is used only for errors, following SBB's rule that red is reserved for errors.

## Known gaps (not yet fixed)
- Hold-to-talk requires press-and-hold, which fails the "no reliance on gestures" and motor-impairment rules. It needs a tap-to-start, tap-to-stop mode.
- Disclosure markers in tool groups still use text glyphs (▸ ▾) rather than drawn icons.
- No icon set yet. When one is needed, draw it in the SBB icon style (1px line, no fill, no rounded line ends, 24 / 36 / 48px) rather than using SBB's icons.
