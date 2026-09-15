"""Adapters turn a prompt into an AgentResult. Anything can be an agent."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Protocol

from ..contract import AgentResult

if TYPE_CHECKING:
    from ..genome import AgentSpec, Genome


class Adapter(Protocol):
    def run(self, task: dict, prompt: str, cancel: threading.Event) -> AgentResult: ...


def build(spec: "AgentSpec", genome: "Genome") -> Adapter:
    if spec.adapter == "scripted":
        from .scripted import ScriptedAdapter
        return ScriptedAdapter(spec, genome.workdir)
    if spec.adapter == "cli":
        from .cli import CliAdapter
        return CliAdapter(spec, genome.root)
    if spec.adapter == "openai":
        from .openai import OpenAIAdapter
        return OpenAIAdapter(spec)
    raise ValueError(f"unknown adapter {spec.adapter!r}")


__all__ = ["Adapter", "build"]
