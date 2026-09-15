---
name: policy
description: Rules as context, permissions as code
type: core
---
# policy

Prose rules travel inside every prompt. Permissions do not rely on the model's good
will: the [[scheduler]] checks them before any agent runs, so a forbidden operation
never reaches it. A broken edit of `policy.yaml` never replaces the last good policy.
Declared in the [[genome]].
