# Agent manifest

This page says what the *agent manifest* is today
(`src/enjambre/schemas/agent-manifest.schema.json`, `src/enjambre/manifest.py` and
`enjambre validate`) and which three related features are **explicitly out of scope** in this
version: admission, quarantine and agent leases. They are not "maybe never": they are roadmap,
written down here so nobody assumes they exist or builds half of one without this context.

## What a manifest is today

A manifest is one `agents.<id>` entry of a `swarm.yaml`, promoted to a standalone document
(the map key becomes the `id` field). `enjambre validate agent.json` checks it against the
JSON Schema. It is the same validation `Genome.load()` already applies when a swarm starts:
the schema cannot drift from real behaviour because the test fixtures are loaded both ways
(`tests/test_manifest_schema.py`).

```bash
pip install 'enjambre[validate]'
enjambre validate agent.json
```

It is **static, offline validation**: one JSON file, one schema, no network, no state, no
kernel involved. It confirms a manifest has the right shape before you paste it into a
`swarm.yaml`, and nothing more.

## Out of scope

### Admission

Adding or removing a live agent without restarting the swarm. Today `agents:` is only read at
start-up (`Genome.load()`). The kernel's hot reload is for `policy.yaml`
(`src/enjambre/policy.py`), not for agents. A manifest that passes `enjambre validate` does
not join a running swarm by itself.

### Quarantine

Isolating an agent automatically when it starts failing. The router already weighs measured
fitness (success, latency) when it scores, and `route: false` in a manifest takes an agent out
of the router's candidates (`Router.choose` in `src/enjambre/router.py`), but that is a manual
switch, not a state the kernel turns on and off by itself when fitness drops. There is no
threshold, no automatic isolation and no readmission.

### Agent lease

Expiring leases (`acquire(resource, holder, minutes)`) cover resources (GPUs, quotas,
browsers), and tasks have their own renewable execution lease. There is no lease on the
agent's identity itself: a provisionally admitted manifest with its own expiry that lapses if
nobody confirms it. It would apply the "nothing blocks forever" principle (principle 3 in
`CONTRIBUTING.md`) to the agent's own life cycle, not only to what it uses.

## Why they are out

Principle 1 in `CONTRIBUTING.md`: *"Every rule comes from a failure. New mechanisms should come
with the incident or the failing test that motivates them."* None of the three has a real
incident behind it in this repository yet, so building them now would be speculative design,
exactly what the project avoids on purpose ("Small on purpose" in `README.md`). They live here
and not in the README's Roadmap because they extend the agent manifest specifically, not the
kernel in general.
