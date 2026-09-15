"""Any OpenAI-compatible chat endpoint: OpenAI, OpenRouter, Ollama, vLLM, LiteLLM...

    agents:
      writer:
        adapter: openai
        options:
          base_url: http://localhost:11434/v1
          model: qwen3:8b
          api_key_env: OLLAMA_API_KEY   # the NAME of the variable, never the key
          timeout_s: 300
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

from ..contract import AgentResult


class OpenAIAdapter:
    def __init__(self, spec) -> None:
        opts = spec.options
        if "api_key" in opts:
            raise ValueError(f"agents.{spec.id}: never put API keys in swarm.yaml; set options.api_key_env instead")
        if not opts.get("model"):
            raise ValueError(f"agents.{spec.id}.options.model is required")
        self.spec = spec
        self.base_url = str(opts.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        self.model = str(opts["model"])
        self.key_env = str(opts.get("api_key_env") or "OPENAI_API_KEY")
        self.timeout_s = float(opts.get("timeout_s", 300))
        self.temperature = opts.get("temperature")
        self.max_tokens = opts.get("max_tokens")

    def run(self, task: dict, prompt: str, cancel: threading.Event) -> AgentResult:
        body: dict = {"model": self.model, "messages": [{"role": "user", "content": prompt}]}
        if self.temperature is not None:
            body["temperature"] = float(self.temperature)
        if self.max_tokens is not None:
            body["max_tokens"] = int(self.max_tokens)
        headers = {"Content-Type": "application/json"}
        key = os.environ.get(self.key_env, "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read(400).decode("utf-8", errors="replace").strip()
            code = "rate_limited" if exc.code == 429 else "provider_error"
            return AgentResult.failed(f"HTTP {exc.code} from {self.base_url}: {detail}", code)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return AgentResult.failed(f"cannot reach {self.base_url}: {type(exc).__name__}", "provider_unreachable")
        if cancel.is_set():
            return AgentResult(status="cancelled", error="cancelled while waiting for the model", error_code="cancelled")
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return AgentResult.failed("the provider returned an unexpected response shape", "provider_bad_response")
        result = AgentResult.parse(text)
        result.meta.setdefault("model", data.get("model") or self.model)
        if isinstance(data.get("usage"), dict):
            result.meta.setdefault("usage", data["usage"])
        return result
