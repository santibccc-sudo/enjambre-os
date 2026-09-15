---
name: proof-of-delivery
description: Existing is not the same as having worked
type: decision
---
# proof of delivery

A task can say what must exist when it is done: a file, a directory or a URL.
Agents declare artifacts too. The [[kernel]] checks them itself, and a file that was
not touched during the task does not count. In `record` mode the verdict is stored;
in `enforce` mode a claim without proof becomes a failure and is retried.
