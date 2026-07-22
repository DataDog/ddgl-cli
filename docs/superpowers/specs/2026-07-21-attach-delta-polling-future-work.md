# `ddgl attach` — delta job-polling (deferred future work)

> **Status: NOT IMPLEMENTED. Deferred.** This captures a design exploration for
> reducing per-tick data volume when attaching to very large pipelines (e.g.
> `datadog-agent`, ~700 jobs). We concluded it cannot be made cleanly correct
> without some form of reconciliation, and shelved it in favour of the simpler,
> fully-correct **parallel pagination** win (see the separate fixes plan). Revisit
> only if parallel-paginated full polls are still too heavy in practice.

## Problem

On a large pipeline, every `attach` poll tick re-fetches the entire job list
(700 jobs → 8 paginated pages). This is slow (~1m30s/tick on datadog-agent before
parallel pagination) and mostly wasted work: between two 10s ticks, only a
handful of jobs actually changed state.

We wanted a *delta* poll: after one initial full fetch, fetch only what changed.

## Hard constraints discovered (GitLab API)

Verified against the GitLab Jobs and Pipelines REST API docs:

1. **No "changed since" filter for jobs.** The pipelines *list* endpoint has
   `updated_after`, but the pipeline-jobs endpoint (`GET
   /projects/:id/pipelines/:pid/jobs`) does **not**. Its only filter is
   `scope[]` (an array of job-status values).
2. **No job-count endpoint.** Neither the pipeline detail nor list endpoint
   carries a per-status job-count breakdown (`detailed_status` describes only
   overall pipeline status). `test_report_summary` counts *tests*, not *jobs*.
   So "how many jobs, how many done" is only knowable by listing jobs.
3. Jobs are returned newest-ID-first; retried jobs are excluded by default
   (`include_retried=false`), so a retry surfaces as a new job ID — convenient
   for ID-keyed local state.

The combination of (1) and (2) is what defeats a clean delta: we can filter by
*status* but cannot ask "what changed" or "how many are there."

## The design we explored

Keep a retained `known: {id → Job}` map, seeded by one full initial fetch.
Partition jobs into three local buckets:

- **terminal** (`success`/`failed`/`canceled`/`skipped`) — immutable, never re-fetched.
- **created** — dormant (DAG jobs waiting on `needs`); on a big pipeline this is
  the *bulk* (~700). Only leaves `created` by starting or pipeline end.
- **volatile** (`pending`/`running`/`preparing`/`waiting_for_resource`/
  `waiting_for_callback`/`manual`/`scheduled`/`canceling`) — the in-flight set;
  small even when total job count is huge.

Steady-state tick = two parallel scoped queries, **both excluding `created`**:

1. `scope=[volatile statuses]` — the in-flight set (dozens, not 700).
2. `scope=[failed, canceled, skipped]` — non-success terminal, to resolve final
   statuses and enable "success by elimination."

`created` and `success` are inferred locally from `known` + the diff. Excluding
`created` from the queries is the key: it's the bulk of the data and it's dormant,
so polling it every tick is the waste we're trying to eliminate. A job leaving
`created` by *starting* is observed when it appears in the volatile query.

## Why it is NOT correct (the fatal caveat)

**A job that runs faster than the poll interval is invisible.** With
`interval=20s`, a job that goes `created → running → success` in 10s — entirely
between two polls — is:

- not in the volatile query at tick N+1 (already terminal),
- not in the non-success-terminal query (it *succeeded*),
- still `created` in our `known` map.

We only ever saw it as `created`, and it silently drops out with no observed
transition. Result: **successful fast jobs are silently undercounted**, and
`known` desyncs permanently (the job is stuck as `created` for the rest of the
attach). The failure is asymmetric and sneaky: a fast `created→running→failed`
job *is* caught (lands in query 2), but the common `created→running→success`
case is not.

This is a direct consequence of the API constraints: with no "changed since"
and no counts, a job can transition through states entirely within a poll gap
and leave no queryable trace.

## Options considered to close the hole

- **A — add `success` to the scoped queries** (poll everything except `created`).
  Correct, but `success` grows monotonically toward ~700 as the pipeline
  completes, so it degrades to a near-full fetch exactly when the pipeline is
  busiest finishing. Still strictly cheaper than a full re-list on a
  created-heavy pipeline (created is always excluded), but the win erodes.
- **B — reconcile against a known total.** No count endpoint exists, so there's
  no cheap total to reconcile against. Rejected.
- **C — poll `created` cheaply.** No count/ID-only mode; `created` *is* the 700.
  Rejected (this is the original problem).
- **D — occasional full resync.** Delta most ticks, one full
  (parallel-paginated, ~2 round-trips) resync every Nth tick and at terminal.
  This is bounded, cheap-ish reconciliation — but it *is* reconciliation, which
  the design was explicitly trying to avoid.
- **E — infer created-departures from a monotonic invariant.** `created` only
  ever decreases; but detecting *which* created jobs left within a gap still
  requires listing created (the 700). Rejected.

## Conclusion

There is no free lunch: **a pure no-reconciliation delta cannot be correct for
sub-interval jobs** given GitLab's API (no "changed since", no counts). The
realistic correct options are A (degrades as pipeline completes) or D (mild
periodic reconciliation). Both add real complexity and correctness-sensitive
logic that needs dedicated tests.

Weighed against **parallel pagination** — which is simple, fully correct, and
collapses a full poll from ~8 sequential round-trips to ~2 — the delta's marginal
benefit did not justify its complexity and risk for v1. **Deferred.** If a
future need arises, Option D (delta + bounded resync) on top of parallel
pagination is the recommended starting point, with tests specifically covering
the fast `created→running→success` case.
