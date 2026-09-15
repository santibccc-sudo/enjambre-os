from pathlib import Path

import pytest

from enjambre.adapters import build
from enjambre.genome import Genome

ROOT = Path(__file__).resolve().parent.parent
GENOMES = sorted(p.parent for p in [*(ROOT / "examples").glob("*/swarm.yaml"),
                                    *(ROOT / "src" / "enjambre" / "templates").glob("*/swarm.yaml")])


@pytest.mark.parametrize("folder", GENOMES, ids=lambda p: p.name)
def test_every_shipped_genome_loads_and_builds(folder):
    genome = Genome.load(folder)
    assert genome.policy.error is None
    assert genome.policy.data is not None
    for spec in genome.agents.values():
        build(spec, genome)
    if genome.memory_dir:
        assert genome.memory_dir.is_dir()


def test_there_is_something_to_check():
    assert {p.name for p in GENOMES} >= {"demo", "real-agents"}
