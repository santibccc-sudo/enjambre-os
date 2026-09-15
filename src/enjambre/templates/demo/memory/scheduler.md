---
name: scheduler
description: claim, route, gate, dispatch, verify, close
type: core
---
# scheduler

The scheduler is just another worker of the [[kernel]]. It claims a task, asks the
[[router]] for an agent unless one is pinned, checks the [[policy]], builds the
prompt, runs one of the [[adapters]] and closes the task with its result. It renews
[[leases]] while agents work and recovers its own tasks after a restart.
