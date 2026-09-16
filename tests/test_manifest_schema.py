"""schemas/agent-manifest.schema.json against tests/fixtures/manifests/*.json.

Valid fixtures are also loaded through Genome.load() as a real swarm.yaml agent,
so the schema cannot drift from what genome.py actually accepts.
"""
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, ValidationError

from enjambre.genome import Genome

ROOT = Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / "schemas" / "agent-manifest.schema.json").read_text())
FIXTURES_DIR = Path(__file__).parent / "fixtures" / "manifests"
VALID_FIXTURES = sorted(p for p in FIXTURES_DIR.glob("*.json") if not p.stem.startswith("invalid-"))
INVALID_FIXTURES = sorted(FIXTURES_DIR.glob("invalid-*.json"))


def test_schema_itself_is_valid():
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", VALID_FIXTURES, ids=lambda p: p.stem)
def test_valid_fixtures_pass_the_schema(path):
    Draft202012Validator(SCHEMA).validate(json.loads(path.read_text()))


@pytest.mark.parametrize("path", INVALID_FIXTURES, ids=lambda p: p.stem)
def test_invalid_fixtures_fail_the_schema(path):
    with pytest.raises(ValidationError):
        Draft202012Validator(SCHEMA).validate(json.loads(path.read_text()))


@pytest.mark.parametrize("path", VALID_FIXTURES, ids=lambda p: p.stem)
def test_valid_fixtures_load_as_a_real_agent(tmp_path, path):
    manifest = json.loads(path.read_text())
    agent_id = manifest["id"]
    entry = {k: v for k, v in manifest.items() if k != "id"}
    (tmp_path / "swarm.yaml").write_text(yaml.safe_dump({"name": "fixture-swarm", "agents": {agent_id: entry}}))
    spec = Genome.load(tmp_path).agents[agent_id]
    assert spec.id == agent_id
    assert spec.adapter == manifest["adapter"]
