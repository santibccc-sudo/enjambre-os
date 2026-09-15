---
name: incidents
description: Why the rules exist
type: history
---
# incidents

- A job that beat every 30 minutes was shown offline two thirds of the time, which
  led to declared periods in [[heartbeats]].
- A render died without releasing a GPU and blocked it for ten hours, which is why
  every lock is one of the [[leases]].
- Agent timeouts equal to the task lease made long work impossible to finish; the
  [[scheduler]] now renews leases.
- Nightly tasks "passed" by pointing at a months-old log, so [[proof-of-delivery]]
  requires freshness.
- A verification gate that blocked closing tasks cut throughput by 90%; proof is now
  recorded by default and enforced only on request.
