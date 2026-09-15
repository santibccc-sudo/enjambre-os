---
name: dead-letter
description: Work that could not be done, always with a reason
type: decision
---
# dead letter

A task dies when its [[leases]] expire after its last attempt, when it sits pending
with no attempts left, or when something upstream in the [[dag]] failed. It always
dies with a reason, and one retry gives it a second life.
