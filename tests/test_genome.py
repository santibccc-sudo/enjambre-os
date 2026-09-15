import pytest

from enjambre.genome import Genome, GenomeError

from .helpers import make_genome


def test_load(tmp_path):
    g = make_genome(tmp_path, """
        scout:
          adapter: scripted
          energy: solar
          cost: 0.5
          options: {delay_s: 0}
    """, policy="rules: [Be kind.]\n", roles={"scout": "You explore."})
    spec = g.agents["scout"]
    assert (spec.adapter, spec.energy, spec.cost, spec.name) == ("scripted", "solar", 0.5, "scout")
    assert g.role("scout") == "You explore."
    assert g.role("nobody") == ""
    assert g.policy.rules_for("scout")["rules"] == ["Be kind."]
    assert g.db_path == tmp_path.resolve() / ".enjambre" / "swarm.db"


@pytest.mark.parametrize("agents, message", [
    ("Bad Name:\n  adapter: scripted", "invalid agent id"),
    ("a:\n  adapter: telepathy", "adapter must be one of"),
    ("a:\n  adapter: scripted\n  energy: nuclear", "energy must be one of"),
    ("a:\n  adapter: scripted\n  options: [1, 2]", "options must be a mapping"),
])
def test_invalid_agents(tmp_path, agents, message):
    with pytest.raises(GenomeError, match=message):
        make_genome(tmp_path, agents)


def test_missing_or_empty(tmp_path):
    with pytest.raises(GenomeError, match="no swarm.yaml"):
        Genome.load(tmp_path)
    (tmp_path / "swarm.yaml").write_text("name: x\n")
    with pytest.raises(GenomeError, match="declares no agents"):
        Genome.load(tmp_path)
    (tmp_path / "swarm.yaml").write_text("agents: [unclosed\n")
    with pytest.raises(GenomeError, match="not valid YAML"):
        Genome.load(tmp_path)
