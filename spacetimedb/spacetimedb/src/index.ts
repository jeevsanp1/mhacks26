/**
 * Claw task/job coordination module.
 *
 * Multiple gateway workers call claim_due_job; the reducer transactionally
 * moves at most one due pending job to running + claimed_by, so only one
 * worker executes each job.
 */
import { SenderError, schema, table, t } from 'spacetimedb/server';

const job = table(
  {
    name: 'job',
    public: true,
    indexes: [
      { accessor: 'by_status_next', algorithm: 'btree', columns: ['status', 'next_run_at_ms'] },
    ],
  },
  {
    id: t.string().primaryKey(),
    name: t.string(),
    enabled: t.bool(),
    status: t.string(), // pending | running | done | error | cancelled
    schedule_json: t.string(),
    payload_json: t.string(),
    session_target: t.string(),
    wake_mode: t.string(),
    delete_after_run: t.bool(),
    next_run_at_ms: t.u64(),
    created_at_ms: t.u64(),
    last_run_at_ms: t.u64(), // 0 = never
    session_key: t.string().index('btree'),
    claimed_by: t.string(), // "" = unclaimed
    claimed_at_ms: t.u64(),
    error: t.string(),
    output: t.string(),
    delivery_json: t.string(), // "" = none
  }
);

const spacetimedb = schema({ job });
export default spacetimedb;

function requireNonEmpty(label: string, value: string): string {
  const v = value.trim();
  if (!v) {
    throw new SenderError(`${label} is required`);
  }
  return v;
}

export const addJob = spacetimedb.reducer(
  {
    id: t.string(),
    name: t.string(),
    enabled: t.bool(),
    scheduleJson: t.string(),
    payloadJson: t.string(),
    sessionTarget: t.string(),
    wakeMode: t.string(),
    deleteAfterRun: t.bool(),
    nextRunAtMs: t.u64(),
    createdAtMs: t.u64(),
    sessionKey: t.string(),
    deliveryJson: t.string(),
  },
  (
    ctx,
    {
      id,
      name,
      enabled,
      scheduleJson,
      payloadJson,
      sessionTarget,
      wakeMode,
      deleteAfterRun,
      nextRunAtMs,
      createdAtMs,
      sessionKey,
      deliveryJson,
    }
  ) => {
    const jobId = requireNonEmpty('id', id);
    if (ctx.db.job.id.find(jobId)) {
      throw new SenderError(`job already exists: ${jobId}`);
    }
    ctx.db.job.insert({
      id: jobId,
      name: name.trim() || 'automation',
      enabled,
      status: 'pending',
      schedule_json: scheduleJson,
      payload_json: payloadJson,
      session_target: sessionTarget || 'current',
      wake_mode: wakeMode || 'now',
      delete_after_run: deleteAfterRun,
      next_run_at_ms: nextRunAtMs,
      created_at_ms: createdAtMs,
      last_run_at_ms: 0n,
      session_key: sessionKey || 'main',
      claimed_by: '',
      claimed_at_ms: 0n,
      error: '',
      output: '',
      delivery_json: deliveryJson || '',
    });
  }
);

/** Atomically claim one due pending job for this worker. */
export const claimDueJob = spacetimedb.reducer(
  {
    workerId: t.string(),
    nowMs: t.u64(),
    sessionKey: t.string(), // "" = any session
  },
  (ctx, { workerId, nowMs, sessionKey }) => {
    const worker = requireNonEmpty('workerId', workerId);
    const sessionFilter = sessionKey.trim();

    // Prefer index prefix on status='pending', then pick earliest due.
    let bestId = '';
    let bestNext = 0n;
    for (const row of ctx.db.job.by_status_next.filter('pending')) {
      if (!row.enabled) continue;
      if (row.claimed_by !== '') continue;
      if (row.next_run_at_ms > nowMs) continue;
      if (sessionFilter && row.session_key !== sessionFilter) continue;
      if (!bestId || row.next_run_at_ms < bestNext) {
        bestId = row.id;
        bestNext = row.next_run_at_ms;
      }
    }
    if (!bestId) {
      return;
    }
    const best = ctx.db.job.id.find(bestId);
    if (!best) {
      return;
    }
    ctx.db.job.id.update({
      ...best,
      status: 'running',
      claimed_by: worker,
      claimed_at_ms: nowMs,
      last_run_at_ms: nowMs,
      error: '',
    });
  }
);

/** Return stale running claims to pending (crashed workers). */
export const reclaimStale = spacetimedb.reducer(
  {
    nowMs: t.u64(),
    timeoutMs: t.u64(),
  },
  (ctx, { nowMs, timeoutMs }) => {
    for (const row of ctx.db.job.iter()) {
      if (row.status !== 'running' || row.claimed_by === '') continue;
      if (row.claimed_at_ms === 0n) continue;
      if (nowMs - row.claimed_at_ms < timeoutMs) continue;
      ctx.db.job.id.update({
        ...row,
        status: 'pending',
        claimed_by: '',
        claimed_at_ms: 0n,
      });
    }
  }
);

export const completeJob = spacetimedb.reducer(
  {
    id: t.string(),
    workerId: t.string(),
    success: t.bool(),
    output: t.string(),
    error: t.string(),
    // After run: delete row, or keep with new status / next fire time.
    remove: t.bool(),
    nextStatus: t.string(), // pending | done | error
    nextRunAtMs: t.u64(),
    enabled: t.bool(),
  },
  (
    ctx,
    { id, workerId, success, output, error, remove, nextStatus, nextRunAtMs, enabled }
  ) => {
    const jobId = requireNonEmpty('id', id);
    const worker = requireNonEmpty('workerId', workerId);
    const row = ctx.db.job.id.find(jobId);
    if (!row) {
      throw new SenderError(`job not found: ${jobId}`);
    }
    if (row.claimed_by && row.claimed_by !== worker) {
      throw new SenderError(`job ${jobId} claimed by ${row.claimed_by}, not ${worker}`);
    }
    if (remove) {
      ctx.db.job.id.delete(jobId);
      return;
    }
    ctx.db.job.id.update({
      ...row,
      status: nextStatus || (success ? 'done' : 'error'),
      enabled,
      next_run_at_ms: nextRunAtMs,
      claimed_by: '',
      claimed_at_ms: 0n,
      output: output || '',
      error: error || '',
    });
  }
);

export const updateJob = spacetimedb.reducer(
  {
    id: t.string(),
    name: t.string(),
    enabled: t.bool(),
    scheduleJson: t.string(),
    payloadJson: t.string(),
    sessionTarget: t.string(),
    wakeMode: t.string(),
    deleteAfterRun: t.bool(),
    nextRunAtMs: t.u64(),
    sessionKey: t.string(),
    status: t.string(),
    deliveryJson: t.string(),
  },
  (
    ctx,
    {
      id,
      name,
      enabled,
      scheduleJson,
      payloadJson,
      sessionTarget,
      wakeMode,
      deleteAfterRun,
      nextRunAtMs,
      sessionKey,
      status,
      deliveryJson,
    }
  ) => {
    const jobId = requireNonEmpty('id', id);
    const row = ctx.db.job.id.find(jobId);
    if (!row) {
      throw new SenderError(`job not found: ${jobId}`);
    }
    ctx.db.job.id.update({
      ...row,
      name: name.trim() || row.name,
      enabled,
      schedule_json: scheduleJson || row.schedule_json,
      payload_json: payloadJson || row.payload_json,
      session_target: sessionTarget || row.session_target,
      wake_mode: wakeMode || row.wake_mode,
      delete_after_run: deleteAfterRun,
      next_run_at_ms: nextRunAtMs,
      session_key: sessionKey || row.session_key,
      status: status || row.status,
      delivery_json: deliveryJson,
      claimed_by: status === 'pending' ? '' : row.claimed_by,
      claimed_at_ms: status === 'pending' ? 0n : row.claimed_at_ms,
    });
  }
);

export const removeJob = spacetimedb.reducer({ id: t.string() }, (ctx, { id }) => {
  const jobId = requireNonEmpty('id', id);
  if (!ctx.db.job.id.find(jobId)) {
    throw new SenderError(`job not found: ${jobId}`);
  }
  ctx.db.job.id.delete(jobId);
});

export const cancelJob = spacetimedb.reducer({ id: t.string() }, (ctx, { id }) => {
  const jobId = requireNonEmpty('id', id);
  const row = ctx.db.job.id.find(jobId);
  if (!row) {
    throw new SenderError(`job not found: ${jobId}`);
  }
  ctx.db.job.id.update({
    ...row,
    status: 'cancelled',
    enabled: false,
    claimed_by: '',
    claimed_at_ms: 0n,
  });
});
