# Claw job coordination (SpacetimeDB)

Transactional task/job queue used when multiple Claw gateways (or channels) may
claim the same due automation.

## Tables

- `job` — OpenClaw-shaped cron jobs (`schedule_json`, `payload_json`, …) plus
  `claimed_by` / `claimed_at_ms` for worker leases.

## Reducers

| Reducer | Purpose |
|---------|---------|
| `add_job` | Insert a pending job |
| `claim_due_job` | Atomically move one due pending job → `running` for a worker |
| `complete_job` | Finish / reschedule / delete after a run |
| `reclaim_stale` | Return timed-out running claims to `pending` |
| `update_job` / `cancel_job` / `remove_job` | Lifecycle |

## Local publish

```bash
# Install CLI: https://spacetimedb.com/install
spacetime start --listen-addr 127.0.0.1:3001
spacetime server add --url http://127.0.0.1:3001 local3001
cd spacetimedb
npm install
spacetime publish claw-jobs -p spacetimedb -s local3001 -y
```

Point Claw at it:

```bash
export CLAW_SPACETIME_URI=http://127.0.0.1:3001
export CLAW_SPACETIME_DB=claw-jobs
claw gateway
```

Without these env vars, Claw uses the local file job store with a directory lock
for single-host exclusive claims.
