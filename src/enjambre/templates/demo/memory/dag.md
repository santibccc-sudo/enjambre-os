---
name: dag
description: Tasks run only when everything upstream is done
type: decision
---
# dag

A task can depend on others. It stays blocked until all of them are done, and it
receives their results as context (marked as data, never as instructions: see
[[policy]]). When an upstream task fails for good, its dependents move to the
[[dead-letter]] state instead of waiting forever; retrying the upstream task
brings them back.
