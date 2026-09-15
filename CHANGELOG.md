# Changelog

## 0.1.0 — unreleased

First version.

- **Kernel** on one SQLite file: derived process state from heartbeats, expiring resource
  leases, durable task queue with priorities, atomic claims, idempotency keys, task leases,
  retries, dead-letter with reasons, DAG dependencies with cascade and revival, cancellation,
  traces and measured fitness.
- **Proof of delivery**: files, directories and URLs checked by the kernel, with freshness;
  `record` or `enforce` mode.
- **Policy**: rules injected into prompts and a permission gate enforced before dispatch;
  hot reload that keeps the last good policy.
- **Router** scoring measured success and latency, declared cost and energy, with a solar
  window. Only agents the policy permits are ranked.
- **Scheduler** with lease renewal, cancellation forwarding and restart recovery.
- **Adapters**: `cli` (no shell, isolated environment, safe on Windows), `openai`
  (any compatible endpoint), `scripted` (demos and tests).
- **MCP server** over stdio, local or remote.
- **HTTP API** and **dashboard** (no build step, strict CSP) with a 3D **memory graph** built
  from markdown notes.
- **CLI**: `demo`, `init`, `up`, `run`, `enqueue`, `tasks`, `ps`, `memory`, `mcp`.
- Tested on Linux, macOS and Windows, Python 3.10 to 3.13.
