"""Scripted agents for demos and tests: no model, no API key, real artifacts."""
from __future__ import annotations

import threading
from pathlib import Path

from ..contract import AgentResult


class ScriptedAdapter:
    """Options: `delay_s` (think time), `fail_first` (fail the first N attempts of each task)."""

    def __init__(self, spec, workdir: Path) -> None:
        self.spec = spec
        self.workdir = Path(workdir)
        self.delay_s = float(spec.options.get("delay_s", 1.0))
        self.fail_first = int(spec.options.get("fail_first", 0))
        self._failures: dict[str, int] = {}
        self._lock = threading.Lock()

    def run(self, task: dict, prompt: str, cancel: threading.Event) -> AgentResult:
        if cancel.wait(self.delay_s):
            return AgentResult(status="cancelled", error="cancelled while working", error_code="cancelled")
        with self._lock:
            failed = self._failures.get(task["id"], 0)
            if failed < self.fail_first:
                self._failures[task["id"]] = failed + 1
                return AgentResult.failed(
                    f"{self.spec.name} stumbled on attempt {failed + 1} (scripted failure)", "scripted_failure")
        self.workdir.mkdir(parents=True, exist_ok=True)
        out = self.workdir / f"{task['id']}-{self.spec.id}.md"
        upstream = prompt.count("UPSTREAM RESULT")
        out.write_text(
            f"# {task['title']}\n\n"
            f"_Written by {self.spec.name} (`{self.spec.id}`)._\n\n"
            f"{task.get('detail') or ''}\n\n"
            f"Upstream results received: {upstream}.\n",
            encoding="utf-8",
        )
        return AgentResult.completed(
            f"{self.spec.name} finished \"{task['title']}\"",
            artifacts=[{"path": str(out), "kind": "file"}],
        )
