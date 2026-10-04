# Phase 20 — Load and failure report

A burst should create a **backlog**, not instability. That is the goal this
report measures, and on the evidence below it largely holds: nothing was lost,
nothing was processed twice, retries work, exhausted jobs end `FAILED`, and the
queue recovers on its own.

**No infrastructure was added.** Redis is not required by these tests and no
broker is started; the task body runs in-process against the real database.
Where a scenario exposed a gap, it is reported below rather than built.

## How to run

```
cd backend
TEST_DATABASE_URL=postgresql://findback:findback@localhost:55432/findback \
  python -m pytest tests/test_phase20_load.py -q -s
```

## Load results

Save-path cost, measured against the real ingest router:

| burst | queued in | per save |
|---|---|---|
| 10 | 0.95 s | 95.1 ms |
| 50 | 1.72 s | 34.4 ms |
| 100 | 3.69 s | 36.9 ms |
| 500 | 18.24 s | 36.5 ms |

Per-save cost is **flat** from 50 to 500. That is the headline number: the save
path is O(1) per save, so a 500-save burst costs 500 fast saves, not one slow
one. The 10-save figure is higher per item only because the first save pays for
connection setup.

In every case `items == jobs`, and no job reached `FAILED`.

Backlog behaviour is what the outbox is for: with the broker down, a burst of
100 becomes 100 `PENDING` jobs, the API honestly reports `pending` for every
one, and per-save cost is unchanged. When the broker returns, the backlog drains
completely — 50 saves, 50 fetches, no duplicates.

## Failure injection results

| injected failure | outcome |
|---|---|
| AI timeout (once) | retry succeeds; job `READY` |
| Embedding timeout (once) | retry succeeds; job `READY` |
| Source unavailable (once) | retry succeeds; job `READY` |
| Redis failure | save still succeeds; backlog drains on recovery |
| Transient DB failure | job row survives; retry succeeds |
| Worker crash | job survives; recovers after the lock goes stale |
| Permanent failure | `FAILED` after 5 attempts; never republished |
| Duplicate save (same URL) | same item returned; fetched **once** |
| Duplicate delivery | completed job is a no-op |
| Two workers, one job | exactly one claim wins |

A useful detail from the retry tests: a failure *after* the fetch stage does
**not** re-fetch the page. Phase 8 resumes at the stage after the last success,
so an AI timeout costs one model call, not a re-crawl. The retry is cheaper than
it looks.

Also verified: a burst containing 20 healthy URLs and 4 dead ones still
completes all 20 healthy ones, with `items == jobs == 24`. Dead links do not
back up the queue.
## Findings

### 1. A crashed job blocks its content for the full lock timeout — by design, but worth knowing

`outbox.py:110` claims a job only while its lock is free:

```sql
AND (locked_at IS NULL
     OR locked_at < now() - make_interval(secs => :lock_secs))
```

A worker killed mid-job skips every `except Exception` handler, so nothing
clears its lock. The job stays `PROCESSING` with a fresh `locked_at`, and
`claim_job` refuses it until `LOCK_TIMEOUT` (5 minutes) elapses.

**This is correct** — refusing is what stops a second worker racing a possibly
live one — and the tests now assert both halves: no pickup while the lock is
fresh, full recovery once it goes stale, no human intervention. But the user
experience is a **5-minute dead window** after any worker crash. A crash of a
50-job worker leaves up to 50 memories invisible for that long.

Reported, not changed: shrinking `LOCK_TIMEOUT` trades this against the risk of
a second worker duplicating a slow-but-live job.

### 2. Celery eager mode silently retries inside one `apply()` call

Not a product bug, but it will bite anyone writing tests against this code
later. In eager mode `task.retry()` **re-runs the task immediately** instead of
scheduling it, so a single `process_item.apply()` performed two full attempts and
reported success — while `attempt_count` recorded 2. Any test written against
`apply()` cannot distinguish one attempt from several, and would conclude that
"retries work" from a call that never actually returned to the caller.

The harness uses `process_item.run()` instead, where each call is exactly one
attempt and a retry surfaces as `celery.exceptions.Retry`.

### 3. The save path has no rate limiting on its own

A 500-save burst costs 500 database writes in 18 s with no backpressure and no
cap. That is exactly what a share sheet or a sync loop can produce, and it is
also a plausible way for one client to monopolise the database. Nothing here
suggests a problem at this scale — Postgres absorbed it without complaint — but
there is no ceiling, and adding one would be a product decision, not a fix
mandated by a failure.

### 4. Duplicate suppression rests on two independent mechanisms

Saving the same URL twice returns the same item and fetches once. That guarantee
## What was NOT tested, and why it matters

- **No real Redis.** Redis failure is injected at the `delay()` boundary, which
  is exactly where a broker outage surfaces in this codebase, but it does not
  exercise broker-side semantics: visibility timeouts, `acks_late` redelivery, or
  a message lost by the broker rather than by a crash in our code.
- **No real worker processes or prefork concurrency.** Every worker here is a
  sequential in-process call. The racing-claim test proves `claim_job` is correct
  under two sequential claimants; it does not prove behaviour under genuine
  parallel load.
- **Single node, single database.** No connection-pool exhaustion, no failover.
- **Backoff is simulated.** Tests clear `available_at` rather than waiting out
  real exponential backoff, so the *timing* is unverified — only the retry
  itself is.
- **The model and fetcher are always instantaneous.** Real per-item latency
  (seconds to minutes) and the resulting queue depth are not modelled, so
  "500 jobs" here is not "500 jobs of real work".
comes from `record_job` being idempotent on the content plus the task's
terminal-state check short-circuiting the second run. The atomic `claim_job`
guard is the separate protection against two *concurrent* workers.

No gap here — both are tested. Worth stating explicitly because they are
different mechanisms, and removing either would reopen duplicate processing.