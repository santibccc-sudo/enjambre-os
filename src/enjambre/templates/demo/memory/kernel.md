---
name: kernel
description: Heartbeats, leases and a durable task queue on one SQLite file
type: core
---
# kernel

The kernel keeps three tables honest:

- **Processes** report [[heartbeats]]; their state is derived, never declared.
- **Resources** are held through [[leases]] that expire on their own.
- **Tasks** live in the [[task-queue]], with a [[dag]], retries and a [[dead-letter]] state.

Before a task is marked done, the kernel checks its [[proof-of-delivery]].
