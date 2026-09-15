"""Policy: rules that travel with every task, plus a deterministic permission gate.

Prose rules are context for the model. The gate is not prose: `check()` runs in
the scheduler before a task reaches any adapter, so a forbidden operation never
gets to the model, whether the model would have obeyed or not.

policy.yaml
-----------
    version: 1
    rules:                       # injected into every prompt
      - id: verify
        rule: Never report work as done without a check that passed.
    operations:                  # operation -> the only agents allowed to run it
      publish: [editor]
    agents:
      researcher:
        forbidden: [Spending money]          # prose, injected into its prompt
        notes: [Prefers primary sources]
        vetoed_operations: [publish]         # enforced
        projects: [docs]                     # enforced: only these projects

Hot reload: the file is re-read when it changes. A broken edit never replaces the
last good policy; the error is reported instead. If no valid policy was ever
loaded, `on_error` decides: "closed" (default) refuses every gated dispatch,
"open" allows it.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Optional

import yaml


class PolicyError(ValueError):
    pass


def _validate(data: Any) -> dict:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise PolicyError("policy must be a mapping")
    rules = data.get("rules") or []
    if not isinstance(rules, list):
        raise PolicyError("'rules' must be a list")
    for rule in rules:
        if not isinstance(rule, (str, dict)) or (isinstance(rule, dict) and "rule" not in rule):
            raise PolicyError(f"invalid rule: {rule!r}")
    ops = data.get("operations") or {}
    if not isinstance(ops, dict) or not all(isinstance(v, list) for v in ops.values()):
        raise PolicyError("'operations' must map operation -> list of agents")
    agents = data.get("agents") or {}
    if not isinstance(agents, dict) or not all(isinstance(v, dict) for v in agents.values()):
        raise PolicyError("'agents' must map agent -> settings")
    for name, cfg in agents.items():
        for key in ("forbidden", "notes", "vetoed_operations", "projects"):
            if key in cfg and not isinstance(cfg[key], list):
                raise PolicyError(f"agents.{name}.{key} must be a list")
    return data


class Policy:
    def __init__(self, path: str | Path | None = None, *, data: dict | None = None, on_error: str = "closed") -> None:
        if on_error not in ("open", "closed"):
            raise ValueError("on_error must be 'open' or 'closed'")
        self.path = Path(path) if path else None
        self.on_error = on_error
        self.error: str | None = None
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._data: dict | None = _validate(data) if data is not None else None
        if self.path is None and data is None:
            self._data = {}

    @property
    def data(self) -> dict | None:
        if self.path is not None:
            self._reload()
        return self._data

    def _reload(self) -> None:
        with self._lock:
            try:
                mtime = os.stat(self.path).st_mtime_ns
            except OSError as exc:
                self.error = f"cannot read {self.path}: {exc.strerror}"
                return
            if mtime == self._mtime:
                return
            self._mtime = mtime
            try:
                self._data = _validate(yaml.safe_load(self.path.read_text(encoding="utf-8")))
                self.error = None
            except (OSError, yaml.YAMLError, PolicyError) as exc:
                self.error = f"invalid policy, keeping the last good one: {exc}"

    def rules_for(self, agent: str = "") -> dict:
        d = self.data or {}
        own = (d.get("agents") or {}).get(agent) or {}
        return {
            "version": d.get("version", 0),
            "agent": agent,
            "rules": [r["rule"] if isinstance(r, dict) else r for r in (d.get("rules") or [])],
            "forbidden": list(own.get("forbidden") or []),
            "notes": list(own.get("notes") or []),
            "error": self.error,
        }

    def prompt_block(self, agent: str) -> str:
        r = self.rules_for(agent)
        if not (r["rules"] or r["forbidden"] or r["notes"]):
            return ""
        lines = ["SWARM RULES (context for you; do not quote them in your answer):"]
        lines += [f"- {rule}" for rule in r["rules"]]
        lines += [f"- FORBIDDEN: {rule}" for rule in r["forbidden"]]
        lines += [f"- note: {note}" for note in r["notes"]]
        return "\n".join(lines)

    def check(self, agent: str, operation: str = "", project: str = "") -> Optional[str]:
        """None if allowed, otherwise a human-readable reason."""
        d = self.data
        if d is None:
            return None if self.on_error == "open" else f"policy unavailable ({self.error})"
        allowed = (d.get("operations") or {}).get(operation) if operation else None
        if allowed is not None and agent not in allowed:
            who = ", ".join(allowed) or "nobody"
            return f"operation '{operation}' is reserved to {who}; requested by '{agent}'"
        own = (d.get("agents") or {}).get(agent) or {}
        if operation and operation in (own.get("vetoed_operations") or []):
            return f"'{agent}' is vetoed from operation '{operation}'"
        scope = own.get("projects") or []
        if project and scope and project not in scope:
            return f"'{agent}' may only work on {', '.join(scope)}, not '{project}'"
        return None
