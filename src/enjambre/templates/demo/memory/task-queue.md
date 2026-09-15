---
name: task-queue
description: Priorities, atomic claims, idempotent requests
type: core
---
# task queue

Tasks have a priority from 1 (urgent) to 9. Claims are atomic: two workers never
get the same task. An idempotency key makes a repeated request return the original
task instead of running the work twice. Tasks can wait for others through the [[dag]].
Part of the [[kernel]].
