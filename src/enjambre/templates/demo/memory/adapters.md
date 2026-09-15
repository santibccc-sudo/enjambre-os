---
name: adapters
description: Anything that can answer a prompt can be an agent
type: guide
---
# adapters

- `cli` runs a command such as Claude Code, Codex or aider, without a shell and with
  an isolated environment.
- `openai` talks to any OpenAI-compatible endpoint: OpenAI, OpenRouter, Ollama, vLLM.
- `scripted` powers this demo: no model, real files.

Every adapter returns the same result contract, used by the [[scheduler]].
