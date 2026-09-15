import re

import pytest

from enjambre.cli import load, main, seed_demo


@pytest.fixture
def swarm(tmp_path):
    target = tmp_path / "demo"
    assert main(["init", str(target)]) == 0
    spec = target / "swarm.yaml"
    spec.write_text(re.sub(r"delay_s: \d+", "delay_s: 0", spec.read_text()).replace("poll_seconds: 1", "poll_seconds: 0.05"))
    return target


def test_init_refuses_to_overwrite(swarm, capsys):
    assert main(["init", str(swarm)]) == 1
    assert "already has a swarm.yaml" in capsys.readouterr().err
    assert main(["init", str(swarm), "--force"]) == 0


def test_the_demo_story_plays_out(swarm):
    genome, kernel = load(swarm)
    research, draft, review = seed_demo(kernel)
    assert seed_demo(kernel) == [research, draft, review]
    assert main(["run", str(swarm), "--timeout", "60"]) == 0
    tasks = {t["title"]: t for t in kernel.tasks(limit=50)}
    assert tasks["Review the brief"]["status"] == "done" and tasks["Review the brief"]["attempts"] == 2
    assert tasks["Publish the brief"]["assignee"] == "scheduler/editor"
    teaser = tasks["Publish a teaser right now"]
    assert teaser["status"] == "failed" and teaser["error_code"] == "permission_denied"
    routed = tasks["Summarise the lease design"]["assignee"]
    assert routed.startswith("scheduler/") and routed != "scheduler/editor"
    assert all(t["verification"] == "verified" for t in tasks.values() if t["status"] == "done")


def test_everyday_commands(swarm, capsys):
    assert main(["enqueue", "Write release notes", "--dir", str(swarm), "--agent", "writer", "--priority", "2"]) == 0
    tid = capsys.readouterr().out.strip()
    assert re.fullmatch(r"[0-9a-f]{12}", tid)
    assert main(["enqueue", "After", "--dir", str(swarm), "--after", "nope"]) == 1
    assert main(["tasks", "--dir", str(swarm)]) == 0
    assert "Write release notes" in capsys.readouterr().out
    assert main(["ps", "--dir", str(swarm)]) == 0
    assert main(["memory", "lease", "--dir", str(swarm)]) == 0
    assert "leases" in capsys.readouterr().out


def test_help_version_and_errors(tmp_path, capsys):
    assert main([]) == 0
    assert "demo" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--version"])
    assert main(["run", str(tmp_path / "missing")]) == 1
    assert "no swarm.yaml" in capsys.readouterr().err
