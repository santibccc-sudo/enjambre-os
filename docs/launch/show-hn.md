# Show HN draft

**Title (max 80 chars for HN):**
Show HN: Enjambre – a durable kernel for swarms of AI agents (Python, MCP)

**URL:** https://github.com/santibccc-sudo/enjambre-os

**Body (goes as the first comment on HN, not in the title field):**

I run a small swarm of AI agents (Claude Code, Codex, a couple of local models) that
works day and night on a solar-powered workstation and a small cloud VPS. Every
popular agent framework I tried (LangGraph, CrewAI, AutoGen) is a library that
orchestrates model calls *inside one Python process*. That's fine until the process
dies — then you lose all state, in-flight work, and any "rule" you wrote only lived
in a prompt the model could ignore.

Enjambre is the opposite bet: a small, boring kernel (one SQLite file, stdlib +
PyYAML) that treats agents as independent processes, the way an OS treats programs.

- Heartbeats derive liveness — a process never *declares* itself alive.
- Every lock (GPU, API quota) is a lease with an expiry. A crash can't hold it forever.
- The task queue has priorities, atomic claims, a DAG, retries, and a dead-letter
  state that always records *why*.
- Permissions are enforced in code before an agent runs, not hoped for in a prompt.
- A router picks the best agent from measured success/latency/cost — and, because
  my swarm runs partly on solar panels, from *energy*: it prefers the solar node
  while the sun is up.
- It talks MCP natively, and its `cli` adapter can drive Claude Code, Codex, or
  anything else on your PATH — no shell, isolated env, safe on Windows too.
- Markdown notes become an explorable, queryable 3D memory graph.

No API key needed to try it:
```
pip install enjambre
enjambre demo
```
That seeds a small editorial pipeline (research → draft → review → publish) with
four scripted agents, opens a dashboard, and shows a policy gate blocking an agent
from publishing without permission.

It's v0.1.0 — young, but every mechanism in it exists because a real incident forced
it (a GPU held for 10 hours by a dead process, a nightly job that looked "offline"
because it only ran every 30 minutes, dependents stuck forever after a partial
retry...). Happy to answer anything about the design or the incidents behind it.

Repo: https://github.com/santibccc-sudo/enjambre-os
Docs / architecture: https://github.com/santibccc-sudo/enjambre-os/blob/main/docs/architecture.md

---

# Reddit draft (r/opensource, r/Python, or r/LocalLLaMA)

**Title:** I open-sourced the durable kernel behind my personal AI agent swarm — Enjambre (Python, SQLite, MCP)

**Body:**

Same pitch as above, adapted with a friendlier opening line, e.g.:

"For the last few months I've been running a small swarm of AI agents (Claude Code,
Codex, a couple of local models) on a solar-powered workstation + a small VPS. I
finally cleaned up and open-sourced the piece that makes it not fall over: a
durable kernel..." (continue with the same bullet list and demo instructions as above)

Close with an honest note: "It's v0.1, single-SQLite-file scale (not built for
thousands of tasks/sec — that's Temporal/Airflow's job), and it has zero users
besides me so far. Feedback and issues very welcome."

---

## Notes for posting
- HN: post from a personal account, ideally with some HN karma already (new accounts'
  "Show HN" posts get less visibility). Best posting time: weekday, ~9-11am US Eastern.
- Reddit: r/opensource and r/Python are stricter about self-promo — read each sub's
  rules first; r/programming sometimes removes pure "look at my project" posts.
- Don't post to all of them at once — HN first, see if it gets traction, then Reddit
  a day or two later with a slightly different angle (the incidents story works well
  as its own post).
