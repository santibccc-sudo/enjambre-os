# Architecture

```text
            MCP clients            HTTP clients            dashboard
       (Claude Code, Codex...)   (scripts, agents)       (browser, no build)
                 │                      │                      │
             enjambre mcp ──────────────┴──── App.handle() ────┘
                                         │
        ┌──────────────┬─────────────────┼──────────────┬───────────────┐
        │              │                 │              │               │
     Kernel         Policy            Router        Scheduler      MemoryGraph
  (SQLite: tasks,  (rules + gate,   (fitness, cost,  (claim, route,  (markdown notes
   deps, traces,    hot reload)      energy, sun)     gate, dispatch, → graph, query,
   leases, runs,                                      renew, close)    pulse)
   processes)                                             │
                                                      Adapters
                                               cli · openai · scripted
```

The HTTP server and the local MCP server call the same `App.handle()`, so every client goes
through the same validation and the same kernel calls.

## Kernel

One SQLite file in WAL mode. Every write is a `BEGIN IMMEDIATE` transaction, which is what makes
claims and leases atomic across threads and processes.

### Processes

`heartbeat(id, period_s=...)` upserts a row. State is derived when listing:

| age of the last beat | state |
|---|---|
| `< max(180 s, 1.2 × period)` | alive |
| `< max(600 s, 2 × period)` | stale |
| older | offline |
| `> max(1 h, 4 × period)` | purged |

A process that comes back after being stale starts a new life (`started_at` resets).

### Leases

`acquire(resource, holder, minutes)` succeeds when the resource is free, expired or already
held by the caller (then it renews and keeps `since`). Leases last 1 to 240 minutes. Only the
holder releases; everyone else waits for the expiry.

### Tasks

```text
             enqueue
                │
                ▼
  ┌────────► pending ──── cancel ────────────────► cancelled
  │             │  (deps done, attempts left)          ▲
  │           claim                                     │
  │             ▼                                       │
  │          running ── cancel requested ── close ──────┤
  │             │                                       │
  │   ┌─────────┼──────────────┬───────────────┐        │
  │ failed,   completed      lease expired   failed,    │
  │ retryable     │          (no attempts)   final      │
  └───┘           ▼               ▼              ▼      │
                 done            dead          failed ──┘ retry → pending
                                   │              │
                         dependents: dead (upstream_failed), revived by retry
```

- **Claim order**: priority (1 first), then creation time.
- **Task lease**: `claim` sets `lease_expires`; `renew` extends it. `rescue()` returns expired
  tasks to pending while attempts remain, otherwise marks them dead with `lease_expired`.
- **Retries**: a failed result goes back to pending while `attempts < max_attempts`, unless its
  `error_code` is non-retryable (`permission_denied`, `upstream_failed`, `invalid_task`,
  `no_agent`, `adapter_config`).
- **Cancellation**: pending tasks are cancelled at once; running ones get `cancel_requested`,
  which the scheduler forwards to the adapter and which turns the eventual result into
  `cancelled`. A cancel request is never lost by a restart or an expired lease.
- **DAG**: `task_deps` holds one row per edge. Dependencies must exist when the task is
  created. Final failure or cancellation of a task kills its pending dependents with
  `upstream_failed`; `retry` of the upstream task revives them.
- **Idempotency**: a repeated `idempotency_key` returns the original task.
- **Results** longer than 8,000 characters are cut with an explicit truncation marker.
- **Traces** record every transition; **runs** record every attempt for fitness.

### Proof of delivery

On a completed result, the kernel checks the task's `proof` and every declared artifact path:

- files must exist, be non-empty and have been modified after the task was created;
- directories must contain something modified after the task was created;
- URLs must answer `HEAD` (or a one-byte `GET`) with a 2xx or 3xx status.

`proof_mode: record` stores `verified`, `rejected` or `no_criteria`. `proof_mode: enforce`
turns a rejected claim into a retryable `proof_missing` failure.

## Scheduler

Each tick: `rescue()`, forward cancellations and renew leases of in-flight tasks, then claim
while there are free slots. For each task:

1. pick the pinned agent, or ask the router;
2. check `policy.check(agent, operation, project)`;
3. reassign the task to `scheduler/<agent>`;
4. build the prompt: role, policy rules, task, upstream results (marked as data), proof,
   output contract;
5. run the adapter in a worker thread, turning any exception into `adapter_crashed`;
6. `complete()` with the result.

On start, tasks left running by a previous instance are recovered, never the ones the current
instance is still running.

## Router

```text
score = Σ weight_k × part_k / Σ weight_k

success  = measured success rate × 100          (prior 70 without runs)
latency  = max(0, 100 − median_ms / 3000)       (prior 50 without runs)
cost     = 100 if cost == 0 else max(0, 100 − 50 × cost)
energy   = solar: 100 in the solar window, 25 outside · always-on: 60 · grid: 40
```

Fitness comes from the `runs` table over the last seven days.

## Policy

`rules` are injected into prompts. `operations`, `vetoed_operations` and `projects` are enforced
by the scheduler before dispatch and exposed through `/api/policy/check` and the MCP
`check_permission` tool. The file is re-read when it changes; invalid edits are reported and
ignored.

## Memory graph

Every `*.md` file under the memory folder (hidden folders excluded) is a node:

- `id` is the slug of the relative path; the label comes from front matter `name`, the first
  `# heading`, or the file name;
- the group comes from front matter `type`, the top-level folder, or `note`; `status:
  archived|deprecated|obsolete` moves a note to `archived`;
- `[[wikilinks]]` resolve by file name, path or name; `[text](other.md)` links resolve by
  relative path and never outside the folder;
- unresolved links become `missing:` nodes.

`pulse(minutes)` reports notes whose content changed recently, using `git log` when the folder
is a repository and file modification times otherwise.

## HTTP API

| Method | Path | |
|---|---|---|
| GET | `/api/health` | no token required |
| GET | `/api/overview` | stats, processes, leases, agents, fitness, router pick |
| GET, POST | `/api/processes`, `/api/heartbeat` | |
| GET | `/api/leases` | |
| POST | `/api/leases/acquire`, `/api/leases/release` | |
| GET, POST | `/api/tasks` | list (`status`, `limit`) · create |
| POST | `/api/tasks/claim` | `worker`, `agents`, `lease_minutes` |
| GET | `/api/tasks/{id}` | with traces |
| POST | `/api/tasks/{id}/complete` `renew` `progress` `cancel` `retry` | |
| GET | `/api/traces` | |
| GET, POST | `/api/policy?agent=`, `/api/policy/check` | |
| GET | `/api/router` | |
| GET | `/api/memory/graph`, `query?q=`, `node?id=`, `pulse?minutes=` | |

Refusals from the kernel (wrong worker, lease held, task not running) return `409` with a
`reason`. Authentication uses `Authorization: Bearer <token>` or `X-Enjambre-Token`.
