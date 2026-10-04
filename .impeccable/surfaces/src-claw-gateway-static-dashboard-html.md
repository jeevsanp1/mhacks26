---
version: 1
slug: "src-claw-gateway-static-dashboard-html"
primary_target: "src/claw/gateway/static/dashboard.html"
related_targets: []
---

## Scope and mode
Operate. The Claw dashboard (gateway web UI) used directly by an older adult. Redesign: replaces the SBB/teal look; keeps content, function, IDs, voice-access behaviour, WCAG 2.2 AAA, light and dark.

## Audience, job, constraints
Older adult talks to Claw (voice first, chat second) and glances at connected data (mock bank, synthetic health). Pinned by the user: the visual language of the Tableau "The Blooming Year" viz (cream paper, double-line frame, giant serif title, tracked small caps, hairlines, rose/sage/olive/forest). Palette confirmed: viz palette, AAA-darkened. Layout confirmed: voice dial in the middle. Ornament confirmed: frame and rules, no chart art.

## Direction contract
THESIS: The dashboard is a printed editorial plate, not an app console: one big serif greeting, one drawn dial you talk to, one ranked list of connections. Refuses the sidebar-plus-chat admin shell and the SBB pill-button system.
OWN-WORLD: Cream paper (#f4ede1) inside a thin double-line frame; deep-rose accent (#751f36) for title and actions; forest, olive, sage for status; hairline rules; tracked uppercase section labels as the headings themselves (no kickers); italic serif captions; serif display (Iowan Old Style / Baskerville stack) and humanist sans body; night paper (#16201b) with cream ink in dark mode. Tick-ring dial drawn in SVG.
STORY: She sees her name, a line saying what to do, and a big dial; taps it, speaks, taps Done; her connections sit as a quiet list she can open.
FIRST VIEWPORT: Three columns on wide screens: left a rose hairline, the greeting in 5rem serif, one plain sentence, today's time/date; centre the 20rem tick-ring dial with its hub button, status and hint beneath, then the conversation; right "Connectors" ranked list with opened data panels below. Single column on small screens with the dial directly under the greeting.
FORM: Editorial data-plate (derived from the user's pinned Tableau reference, not the roll). Seed key: pinned-by-user, no roll.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved
Sample-data labelling, copy rewrite, persistent Stop/Undo, live-region announcements remain out of this pass (from critique P1/P2).
