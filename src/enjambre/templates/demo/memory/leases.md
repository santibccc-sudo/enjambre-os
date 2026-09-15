---
name: leases
description: Every lock expires, so nobody has to clean up after a crash
type: decision
---
# leases

A GPU, an API quota or a browser session is held through a lease with an expiry.
A holder that crashes loses it when the lease runs out. Running tasks are leases
too: the [[scheduler]] renews them while an agent works, and the [[kernel]] takes
them back from silent workers. Related: [[dead-letter]].
