---
name: heartbeats
description: A dead process cannot lie, it just stops beating
type: decision
---
# heartbeats

Processes never say "I am alive". They beat, and the [[kernel]] derives *alive*,
*stale* or *offline* from the age of the last beat. Jobs that run every half hour
declare their period so they do not look dead between runs. See [[incidents]].
