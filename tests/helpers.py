import textwrap
from pathlib import Path

from enjambre.genome import Genome


def make_genome(root: Path, agents: str, *, policy: str | None = None, extra: str = "", roles: dict | None = None) -> Genome:
    root.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(extra).strip() + "\n" if extra else ""
    (root / "swarm.yaml").write_text(
        "name: test-swarm\nscheduler:\n  poll_seconds: 0.05\n  max_parallel: 3\n" + body
        + "agents:\n" + textwrap.indent(textwrap.dedent(agents).strip(), "  ") + "\n"
    )
    if policy is not None:
        (root / "policy.yaml").write_text(textwrap.dedent(policy))
    for agent_id, text in (roles or {}).items():
        (root / "prompts").mkdir(exist_ok=True)
        (root / "prompts" / f"{agent_id}.md").write_text(text)
    return Genome.load(root)
