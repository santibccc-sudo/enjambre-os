"""The genome: a whole swarm declared in text files you can version in git.

    my-swarm/
    ├── swarm.yaml        agents, router, scheduler, kernel, memory
    ├── policy.yaml       rules and the permission gate (hot-reloaded)
    ├── prompts/<id>.md   the role of each agent
    └── memory/           markdown notes, rendered as the memory graph
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .policy import Policy

ADAPTERS = ("scripted", "cli", "openai")
ENERGY = ("solar", "grid", "always-on")
_AGENT_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class GenomeError(ValueError):
    pass


@dataclass
class AgentSpec:
    id: str
    adapter: str
    name: str = ""
    host: str = ""
    energy: str = "always-on"
    cost: float = 0.0
    route: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class Genome:
    root: Path
    name: str
    agents: dict[str, AgentSpec]
    kernel: dict[str, Any]
    scheduler: dict[str, Any]
    router: dict[str, Any]
    memory_dir: Path | None
    policy: Policy

    @classmethod
    def load(cls, root: str | Path) -> "Genome":
        root = Path(root).resolve()
        spec_file = root / "swarm.yaml"
        if not spec_file.is_file():
            raise GenomeError(f"no swarm.yaml in {root}")
        try:
            data = yaml.safe_load(spec_file.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise GenomeError(f"swarm.yaml is not valid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise GenomeError("swarm.yaml must be a mapping")

        agents: dict[str, AgentSpec] = {}
        for aid, cfg in (data.get("agents") or {}).items():
            aid = str(aid)
            if not _AGENT_ID.match(aid):
                raise GenomeError(f"invalid agent id {aid!r}: use lowercase letters, digits, '-' and '_'")
            cfg = cfg or {}
            if not isinstance(cfg, dict):
                raise GenomeError(f"agents.{aid} must be a mapping")
            if cfg.get("adapter") not in ADAPTERS:
                raise GenomeError(f"agents.{aid}.adapter must be one of: {', '.join(ADAPTERS)}")
            energy = cfg.get("energy", "always-on")
            if energy not in ENERGY:
                raise GenomeError(f"agents.{aid}.energy must be one of: {', '.join(ENERGY)}")
            options = cfg.get("options") or {}
            if not isinstance(options, dict):
                raise GenomeError(f"agents.{aid}.options must be a mapping")
            agents[aid] = AgentSpec(
                id=aid,
                adapter=cfg["adapter"],
                name=str(cfg.get("name") or aid),
                host=str(cfg.get("host") or ""),
                energy=energy,
                cost=float(cfg.get("cost") or 0),
                route=bool(cfg.get("route", True)),
                options=dict(options),
            )
        if not agents:
            raise GenomeError("swarm.yaml declares no agents")

        policy_file = root / str(data.get("policy") or "policy.yaml")
        memory = data.get("memory") or {}
        memory_dir = (root / str(memory["dir"])).resolve() if memory.get("dir") else None
        return cls(
            root=root,
            name=str(data.get("name") or root.name),
            agents=agents,
            kernel=dict(data.get("kernel") or {}),
            scheduler=dict(data.get("scheduler") or {}),
            router=dict(data.get("router") or {}),
            memory_dir=memory_dir,
            policy=Policy(policy_file) if policy_file.exists() else Policy(),
        )

    @property
    def db_path(self) -> Path:
        return (self.root / str(self.kernel.get("db") or ".enjambre/swarm.db")).resolve()

    @property
    def workdir(self) -> Path:
        return self.root / ".enjambre" / "work"

    def role(self, agent_id: str) -> str:
        path = self.root / "prompts" / f"{agent_id}.md"
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""
