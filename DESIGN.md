---
name: EWOK
description: A calm, white-and-mist, teal interface for an older adult's AI assistant, built on the SBB design system's structure, tokens and principles, in EWOK's own palette (working name; final name pending).
colors:
  white: "#ffffff"
  mist: "#e7ecef"
  mist-deep: "#d3dbe0"
  ink: "#272932"
  ink-soft: "#454956"
  slate: "#757c8a"
  teal: "#0d6263"
  teal-hover: "#0a4e4f"
  teal-active: "#083d3d"
  coral: "#f05d5e"
  coral-deep: "#940e0f"
  tan-soft: "#e2b794"
  tan-deep: "#91562c"
  dark-page: "#1b1d25"
  dark-subtle: "#272932"
  dark-muted: "#363a49"
  dark-text: "#e7ecef"
  dark-text-soft: "#c9ccd4"
  dark-border: "#6c727f"
  dark-teal: "#6ec2c4"
  dark-coral-text: "#f9bebe"
  dark-tan: "#dbac8a"
typography:
  display:
    fontFamily: "Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "2rem"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0em"
  headline:
    fontFamily: "Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "1.5rem"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0em"
  title:
    fontFamily: "Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0em"
  body:
    fontFamily: "Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.75
    letterSpacing: "0.03em"
  label:
    fontFamily: "Helvetica Neue, Helvetica, Arial, sans-serif"
    fontSize: "1rem"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "0.03em"
  mono:
    fontFamily: "ui-monospace, SFMono-Regular, Consolas, Menlo, monospace"
    fontSize: "1rem"
rounded:
  sm: "0.25rem"
  md: "0.5rem"
  lg: "0.75rem"
  xl: "1rem"
  pill: "999px"
spacing:
  xs: "0.25rem"
  sm: "0.5rem"
  md: "1rem"
  lg: "1.5rem"
  xl: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.teal}"
    textColor: "{colors.white}"
    rounded: "{rounded.pill}"
    height: "3rem"
    padding: "0.25rem 1.25rem"
  button-primary-hover:
    backgroundColor: "{colors.teal-hover}"
  button-primary-active:
    backgroundColor: "{colors.teal-active}"
  button-primary-disabled:
    backgroundColor: "{colors.mist}"
    textColor: "{colors.slate}"
  button-secondary:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pill}"
    height: "3rem"
    padding: "0.25rem 1.25rem"
  input-text:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "3rem"
    padding: "0.5rem 0.75rem"
  card:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "1rem"
  widget:
    backgroundColor: "{colors.mist}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "1rem 1.25rem"
  message-user:
    backgroundColor: "{colors.mist-deep}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "0.75rem 1.25rem"
  message-assistant:
    backgroundColor: "{colors.white}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "0.75rem 1.25rem"
  badge:
    backgroundColor: "{colors.mist}"
    textColor: "{colors.ink}"
    rounded: "{rounded.sm}"
    padding: "0 0.75rem"
---

# Design System: EWOK

*Foundation: the SBB design system (digital.sbb.ch, Lyne v5.7.0). Structure, principles, spacing, type scale, radii, elevation, motion, breakpoints and UX-writing rules are SBB's. Colours are the user's palette (coolors.co/f05d5e-0f7173-e7ecef-272932-d8a47f), which replaces SBB's colours. Elsewhere, where SBB and an earlier EWOK decision disagree, SBB wins. Behavioral and writing rules live in `UX-GUIDELINES.md`. Tokens live in `src/claw/gateway/static/tokens.css` and are the code source of truth.*

## Overview

**Creative North Star: "The Station Concourse"**

A public concourse works because it is calm, legible and obvious: wide white space, large unambiguous signs, one clear route at a time, and nothing that makes a first-time visitor feel stupid. EWOK borrows that. White and mist carry about 60% of every screen, ink text and neutrals about 25%, teal about 10% for the one thing to do next, and coral and tan the remaining 5%. Type is large, spacing is generous, controls are big and round, and corners soften without turning toy-like. The restraint echoes 2010s Apple without imitating it.

SBB's principles govern decisions: User-centred, Recognisable, Inclusive, Reduced ("as little as possible, as much as necessary"), Holistic, Self-explanatory, Task-oriented, Appropriate. Inclusive and Reduced weigh most for an older-adult product.

**Key Characteristics:**
- 60 / 25 / 10 / 5 colour ratio: white and mist, ink and neutrals, teal, coral and tan.
- WCAG 2.2 AAA: 7:1 text, 44px minimum targets, 3px focus rings, never colour alone.
- Large, plain type: 18px body, nothing below 16px.
- One primary action per view; large pill buttons.
- Everything native: real buttons, links, labels, headings, landmarks.

## Colors

The user's palette (coolors.co/f05d5e-0f7173-e7ecef-272932-d8a47f: teal, coral, mist, ink, tan). The raw teal (#0f7173) and raw tan (#d8a47f) are not used: they miss AAA for text. Only AAA-clean lightness steps of the palette hues are defined, so every shade is recognisably from the palette. Light and dark follow the system setting (`color-scheme: light dark`, `light-dark()`, as SBB does) until the person flips the header switch, which then overrides it.

| Role | Light | Dark |
|---|---|---|
| Page | White `#ffffff` | `#1b1d25` |
| Subtle surface (rail, widgets, headers) | Mist `#e7ecef` | `#272932` |
| Muted (user bubble, hover, dividers in dark) | Mist Deep `#d3dbe0` | `#363a49` |
| Text | Ink `#272932` (14.5:1) | `#e7ecef` (14.1:1) |
| Secondary text | Ink Soft `#454956` (9.0:1; not on Mist Deep) | `#c9ccd4` (7.0:1+) |
| Control border (3:1+) | Slate `#757c8a` | `#6c727f` |
| Primary fill / teal | Teal `#0d6263` (7.1:1 with white text) | `#6ec2c4` (8.3:1 with dark text) |
| Primary hover / active | `#0a4e4f` / `#083d3d` | `#80cacb` / `#92d1d3` |
| Error text | Coral Deep `#940e0f` (9.0:1) | `#f9bebe` (7.1:1+) |
| Error icon / border | Coral `#f05d5e` (3.3:1 on white) | Coral (4.4:1) |
| Warning / sample-data | Tan Soft `#e2b794` fill with ink text (7.9:1); Tan Deep `#91562c` border | Tan `#dbac8a` border and text (7.1:1) |

Text on primary is white in light mode and the dark page colour in dark mode. Success and info use the primary teal and always come with a word.

### Named Rules
**The Teal Rule.** Teal marks the next step. If more than one thing on a screen is teal-filled, none of them is.
**The Coral-Is-Errors Rule.** Coral is for errors only (SBB reserves red for errors; the rule carries over).
**The Words-Beside-Colour Rule.** Every status colour is paired with a word ("Connected", "Error", "Not loaded yet"). Colour alone never carries meaning.
**The Honest Data Rule.** Mock and sample data is labelled as such, in the warning treatment, never in the success treatment.
**The Palette-Steps-Only Rule.** If a colour fails AAA for its job, take a lightness step of the same palette hue. Never substitute an unrelated colour, and never use a raw palette colour that fails.

## Typography

**Font:** Helvetica Neue, Helvetica, Arial, sans-serif. SBB's published fallback chain. The licensed SBB typeface is not used or hotlinked.
**Mono:** `ui-monospace, SFMono-Regular, Consolas, Menlo`, only for tool names, arguments and code.

**Character:** Plain, humanist-grotesque, large. Hierarchy comes from size and weight (700), never from uppercase or tracking.

### Hierarchy
- **Display** (700, 2rem, 1.4): Widget figures and greetings.
- **Headline** (700, 1.5rem, 1.4): Page title.
- **Title** (700, 1.25rem, 1.4): Section and panel headings.
- **Body** (400, 1.125rem, 1.75, +0.03em): Chat, summaries, copy. Measure capped at 65ch.
- **Label** (700, 1rem, 1.4): Field labels, badges, state text, table text. 1rem is the floor.

### Named Rules
**The Sixteen-Pixel Floor.** No text under 1rem (SBB's recommended minimum, and AAA for this audience).
**The Sentence-Case Rule.** Sentence case everywhere (SBB: proven more readable). Product names only are capitalised. No all-caps labels.
**The Heading Order Rule.** One h1, then h2, h3 in order. Visual size is set separately from semantic level.

## Layout

SBB grid: 4 / 8 / 12 / 16 columns at the zero / small / large / ultra breakpoints (below 600px, 600px, 1024px, 1440px), gutters 1 / 1.5 / 2 / 2rem, page offsets 1.25 / 3 / 4 / 7.5rem, max content width 75rem. The EWOK app shell is a full-height three-pane (connectors 17rem, content, chat 24–32rem) and, like SBB's own sidebar pattern, is exempt from the 75rem cap; reading content is still capped at 65ch. Below 1024px the shell becomes one column and the page scrolls; nothing is squeezed into a fixed-height region. Spacing follows SBB's 4px fixed scale (0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4rem).

## Elevation & Depth

Tonal first, shadow second. Surfaces separate by white / mist / mist-deep fills and hairline dividers. SBB's layered elevation appears only as a response: level 3 (`0 0.125rem 1rem` + `0 0.0625rem 0.25rem`, ink at 10% and 20%) on button and card hover; level 9 is reserved for dialogs. Shadows are soft and offset, never hard or coloured.

### Named Rules
**The Quiet Lift Rule.** Elevation answers an interaction (hover, an open dialog); it is not a resting decoration.

## Shapes

SBB radius scale: 4px small elements and inline code, 8px cards / inputs / panels / widgets, 12px message bubbles, 16px dialogs, full pill for buttons and the round hold-to-talk control. Borders are 1px for fields and dividers, 2px for cards and buttons, 3px for the open-connector state, 4px for the active-tab underline. The 3px focus ring sits 3px outside the element.

## Components

### Buttons
- **Shape:** Pill, 2px border, 48px minimum height (56px for the main action, never below 44px).
- **Primary:** Teal fill, white bold text (dark text in dark mode). Hover and active step darker (lighter in dark mode); hover adds the level-3 shadow.
- **Secondary:** White fill, Ink text, Slate 2px border; hover Mist Deep fill.
- **Disabled:** Mist fill, Slate text, 1px dashed Slate border.
- **Focus:** 3px solid Ink ring, 3px offset.
- **Rule:** One primary per view or action group (2–3 items max, "back/cancel" left, continue right). Labels are 1–4 words and start with a verb.

### Inputs / Fields
- **Style:** White, 1px Slate border, 8px radius, 48px minimum height, 1.125rem text. A visible label sits above, always. Placeholder shows an example and never replaces the label.
- **Focus:** 3px Black ring.
- **Error:** Coral Deep text below the field that says how to fix it, with an error icon-or-word, never colour alone.

### Cards (connectors, panels, widgets)
- **Style:** 8px radius; White with a 2px Mist border (connector, panel) or Mist fill with no border (widget). Open connector: 3px Teal border.
- **Content:** Title, one line of description, then a state word.

### Badges / status
- **Style:** 4px radius, 1px border in the status colour, Subtle fill (warning fill for sample data, page colour for errors), Ink bold text, 1rem. The word carries the meaning; the border colour reinforces it.

### Theme toggle
- **Style:** A plain button in the header, 44px tall, with a drawn track and knob. The label names the mode you will switch to ("Dark mode" in light, "Light mode" in dark); the knob position shows the current mode. Because the label changes, it is not a `role="switch"`. Starts from the system setting, saves an explicit choice, and applies before first paint.

### Message bubbles
- **Style:** 12px radius. User: muted fill, right-aligned. Assistant: White, 2px Mist border, left-aligned. 65ch maximum width.

### Tabs
- **Default:** The page opens on Voice; `/#chat` opens Chat.
- **Style:** Equal-width text buttons, 56px tall, `role="tab"` with `aria-selected`. Active: Ink text, 4px Teal underline. Inactive: Ink Soft text.

### Notifications (when added)
- **Types:** info, success, warn, error, note, with an icon, optional title, message and optional action, and optional close. 8px radius, 1px border, Ink text. Alerts and notifications are never combined; alerts are never used for form errors. Toasts are short-lived, one at a time, with an Undo option for reversible actions; use sparingly because they stress people with visual impairments.

### Dialogs (when added)
- **Style:** 16px radius, level-9 shadow, one task, a title, closable by button, Esc and backdrop (unless it blocks a critical action). Destructive or money-moving actions use a confirmation dialog that states the consequence ("Delete entry? This action cannot be undone.").

## Do's and Don'ts

### Do:
- **Do** keep white and mist at about 60% of any screen and teal at about 10%.
- **Do** use the SBB spacing scale and radius scale, and the tokens in `tokens.css`; never hard-code a value.
- **Do** make every tappable target at least 44 x 44px and every text at least 16px.
- **Do** pair every colour signal with a word, shape or underline.
- **Do** use native elements, visible labels, skip links and landmarks.
- **Do** write sentence case, short verb-first button labels, descriptive link text, and errors that tell people how to fix them.
- **Do** support light and dark from the same tokens, solved separately for AAA.
- **Do** respect `prefers-reduced-motion`; use SBB's 0.12–0.24s durations.

### Don't:
- **Don't** use the SBB logo, signet, icons, pictograms, timetable icons, clock or typeface. SBB's rights of use restrict them to SBB and Swiss public transport.
- **Don't** use coral for anything but errors.
- **Don't** use the raw palette teal (#0f7173) or tan (#d8a47f); use the AAA steps.
- **Don't** use all-caps labels, placeholder-only labels, "Click here", or "Continue" repeated without context.
- **Don't** use more than one primary button per view, or put explanation inside a button.
- **Don't** rely on hover, drag, multi-touch or long-press as the only way to do something.
- **Don't** use Unicode glyphs or emoji as icons, and don't add decorative shadows or gradients.
