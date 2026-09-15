import os
import time

import pytest

from enjambre.policy import Policy, PolicyError

POLICY = """
version: 3
rules:
  - id: verify
    rule: Never report work as done without a check that passed.
  - Be brief.
operations:
  publish: [editor]
agents:
  researcher:
    forbidden: [Spending money]
    notes: [Prefers primary sources]
    vetoed_operations: [deploy]
    projects: [docs]
"""


@pytest.fixture
def policy_file(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY)
    return p


def test_gate(policy_file):
    pol = Policy(policy_file)
    assert pol.check("editor", "publish") is None
    assert "reserved to editor" in pol.check("researcher", "publish")
    assert "vetoed" in pol.check("researcher", "deploy")
    assert pol.check("researcher", project="docs") is None
    assert "may only work on docs" in pol.check("researcher", project="billing")
    assert pol.check("anyone", "unlisted-operation") is None


def test_prompt_block(policy_file):
    block = Policy(policy_file).prompt_block("researcher")
    assert "Never report work as done" in block
    assert "- Be brief." in block
    assert "FORBIDDEN: Spending money" in block
    assert "note: Prefers primary sources" in block


def test_hot_reload_and_broken_edits_keep_the_last_good_policy(policy_file):
    pol = Policy(policy_file)
    assert pol.check("researcher", "publish")
    policy_file.write_text(POLICY.replace("[editor]", "[editor, researcher]"))
    os.utime(policy_file, (time.time() + 5, time.time() + 5))
    assert pol.check("researcher", "publish") is None
    policy_file.write_text("operations: [this is: not valid")
    os.utime(policy_file, (time.time() + 10, time.time() + 10))
    assert pol.check("researcher", "publish") is None
    assert "keeping the last good one" in pol.error


def test_without_any_valid_policy_the_gate_closes_unless_told_otherwise(tmp_path):
    missing = tmp_path / "nope.yaml"
    assert "policy unavailable" in Policy(missing).check("a", "publish")
    assert Policy(missing, on_error="open").check("a", "publish") is None


def test_empty_policy_allows_and_injects_nothing():
    pol = Policy()
    assert pol.check("a", "publish") is None
    assert pol.prompt_block("a") == ""


@pytest.mark.parametrize("bad", [
    ["a list"],
    {"rules": "not a list"},
    {"operations": {"publish": "editor"}},
    {"agents": {"x": {"projects": "docs"}}},
    {"rules": [{"id": "no-rule-text"}]},
])
def test_validation(bad):
    with pytest.raises(PolicyError):
        Policy(data=bad)
