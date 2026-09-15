import os
import shutil
import subprocess
import time

import pytest

from enjambre.memory import MemoryGraph, slug


@pytest.fixture
def notes(tmp_path):
    root = tmp_path / "memory"
    (root / "decisions").mkdir(parents=True)
    (root / ".obsidian").mkdir()
    (root / "kernel.md").write_text(
        "---\nname: The Kernel\ndescription: Leases, queue and heartbeats\ntype: core\ntags: [kernel]\n---\n"
        "The kernel links to [[Lease rescue]] and [[queue|the queue]] and [[ghost-note]].\n")
    (root / "queue.md").write_text("# Durable queue\n\nClaims are atomic. See [the kernel](kernel.md).\n")
    (root / "decisions" / "lease-rescue.md").write_text(
        "---\nname: Lease rescue\ntype: decision\n---\nSilent workers lose their task. Back to [[kernel]].\n")
    (root / "old.md").write_text("---\nstatus: deprecated\n---\n# Old router\n\nReplaced by [[kernel]]. [out](../../etc/passwd.md)\n")
    (root / ".obsidian" / "hidden.md").write_text("[[kernel]]")
    return root


def test_graph_structure(notes):
    g = MemoryGraph(notes).graph()
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"kernel", "queue", "decisions-lease-rescue", "old", "missing:ghost-note"}
    links = {(l["source"], l["target"]) for l in g["links"]}
    assert ("decisions-lease-rescue", "kernel") in links
    assert ("kernel", "queue") in links
    assert ("kernel", "missing:ghost-note") in links
    assert ("kernel", "old") in links
    by_id = {n["id"]: n for n in g["nodes"]}
    assert by_id["kernel"]["label"] == "The Kernel" and by_id["kernel"]["group"] == "core"
    assert by_id["queue"]["label"] == "Durable queue" and by_id["queue"]["description"] == "Claims are atomic. See [the kernel](kernel.md)."
    assert by_id["old"]["group"] == "archived"
    assert by_id["missing:ghost-note"]["group"] == "missing"
    assert by_id["kernel"]["degree"] == 4
    assert g["stats"] == {"nodes": 5, "notes": 4, "missing": 1, "links": 4}


def test_query_and_node(notes):
    mem = MemoryGraph(notes)
    res = mem.query("lease")
    assert res["results"][0]["id"] == "decisions-lease-rescue"
    assert "The Kernel" in res["results"][0]["neighbors"]
    assert mem.query("atomic")["results"][0]["id"] == "queue"
    assert mem.query("a")["results"] == []
    node = mem.node("kernel")
    assert set(node["neighbor_ids"]) == {"queue", "decisions-lease-rescue", "missing:ghost-note", "old"}
    assert mem.node("nope") is None


def test_pulse_with_mtime(notes):
    old = time.time() - 7_200
    for f in notes.rglob("*.md"):
        os.utime(f, (old, old))
    (notes / "queue.md").write_text("# Durable queue\n\nchanged\n")
    pulse = MemoryGraph(notes, cache_s=0).pulse(minutes=30)
    assert pulse["source"] == "mtime" and list(pulse["alive"]) == ["queue"]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_pulse_prefers_git_over_mtime(notes):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    subprocess.run(["git", "init", "-q"], cwd=notes, check=True, env=env)
    subprocess.run(["git", "add", "kernel.md"], cwd=notes, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "kernel"], cwd=notes, check=True, env=env)
    pulse = MemoryGraph(notes, cache_s=0).pulse(minutes=30)
    assert pulse["source"] == "git" and list(pulse["alive"]) == ["kernel"]


def test_groups_get_distinct_colours(tmp_path):
    for i, group in enumerate(["core", "decision", "guide", "history", "idea", "person", "place"]):
        (tmp_path / f"n{i}.md").write_text(f"---\ntype: {group}\n---\n# note {i}\n\n[[nowhere-{i}]]\n")
    groups = MemoryGraph(tmp_path).graph()["groups"]
    colours = [g["color"] for g in groups]
    assert len(set(colours)) == len(colours)
    assert {g["name"]: g["color"] for g in groups}["missing"] == "#ff5b6e"


def test_slug_and_empty_folders(tmp_path):
    assert slug("Decisión: Lease/Rescue!") == "decision-lease-rescue"
    assert MemoryGraph(tmp_path / "nothing").graph()["stats"]["nodes"] == 0
