#!/usr/bin/env bash
# Thin wrapper around generic automations (OpenClaw-shaped cron job write).
# Prefer: claw automations --at 1m -m "…"   or the agent `automations` tool.
set -euo pipefail
DELAY="${1:-1m}"
SESSION="${SESSION:-main}"
MESSAGE="${2:-Run the scheduled agent turn after ${DELAY}.}"
exec claw automations --at "$DELAY" -s "$SESSION" -m "$MESSAGE" --name "oneshot"
