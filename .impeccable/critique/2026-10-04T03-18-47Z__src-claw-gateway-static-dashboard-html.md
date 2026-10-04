---
target: the dashboard
total_score: 20
max_score: 40
na_heuristics: 
p0_count: 1
p1_count: 3
target_identity: "file:/Users/sanjeevvijay/Documents/GitHub/mhacks26/src/claw/gateway/static/dashboard.html"
target_fingerprint: "sha256:b1d7909efbc9d460662c16b04f120420d3e3df31ef0c712a856cef9bdd25a475"
target_path: /Users/sanjeevvijay/Documents/GitHub/mhacks26/src/claw/gateway/static/dashboard.html
timestamp: 2026-10-04T03-18-47Z
slug: src-claw-gateway-static-dashboard-html
---
# Critique: dashboard (20/40, Acceptable)

Method: A (design review, source-only) + B (detector; 0 findings; browser overlay skipped, no browser tooling).

Heuristics: 1:2 2:1 3:2 4:3 5:2 6:2 7:2 8:3 9:2 10:1 = 20/40.
Cognitive load: 5/8 checklist failures (critical).

Priority issues:
- [P0] Voice action is mouse/touch hold-only (dashboard.html:1346-1350). Fix: tap-to-toggle, keyboard, Listening state + Cancel, drop mouseleave. -> harden
- [P1] Honest-data rule not enforced: .dot.mock/.badge.mock never applied (:204,:231,:603,:663). -> clarify
- [P1] Developer-facing copy: nessie/FinchNode, tool names, JSON, H/LL flags, raw err.message. -> clarify, distill
- [P1] IA not voice-first; no visible Stop/Undo, memory, caps, scam flags; voice is third column / below fold on mobile. -> layout, shape
- [P2] Status and auto-opened panels not announced; mic requested on load. -> onboard, harden

Personas: Jordan, Sam, Margaret (older adult, low vision/hearing loss/low tech confidence).
