from datetime import datetime, timezone

from enjambre import AgentResult, Kernel
from enjambre.router import Router

from .helpers import make_genome

AGENTS = """
sun:
  adapter: scripted
  energy: solar
vps:
  adapter: scripted
  energy: always-on
"""
WINDOW = """
router:
  solar_window: {start: "10:00", end: "17:00", timezone: UTC}
"""


def _at(hour: int) -> float:
    return datetime(2026, 6, 21, hour, 0, tzinfo=timezone.utc).timestamp()


def test_solar_agents_win_while_the_sun_is_up(tmp_path):
    g = make_genome(tmp_path, AGENTS, extra=WINDOW)
    k = Kernel(tmp_path / "k.db")
    noon = Router(g, k, clock=lambda: _at(12)).choose()
    night = Router(g, k, clock=lambda: _at(22)).choose()
    assert noon["agent"] == "sun" and noon["solar"]
    assert night["agent"] == "vps" and not night["solar"]
    assert "no data yet" in night["reason"]


def test_overnight_windows(tmp_path):
    g = make_genome(tmp_path, AGENTS, extra='router:\n  solar_window: {start: "22:00", end: "06:00", timezone: UTC}')
    k = Kernel(tmp_path / "k.db")
    assert Router(g, k, clock=lambda: _at(23)).solar_active()
    assert Router(g, k, clock=lambda: _at(3)).solar_active()
    assert not Router(g, k, clock=lambda: _at(12)).solar_active()


def test_measured_fitness_beats_priors(tmp_path):
    g = make_genome(tmp_path, AGENTS, extra=WINDOW)
    k = Kernel(tmp_path / "k.db")
    for _ in range(3):
        tid = k.enqueue("t", max_attempts=1)["id"]
        k.claim("scheduler/sun")
        k.complete(tid, "scheduler/sun", AgentResult.failed("no"))
    router = Router(g, k, clock=lambda: _at(12))
    ranking = {r["agent"]: r for r in router.choose()["ranking"]}
    assert ranking["sun"]["measured"] and ranking["sun"]["parts"]["success"] == 0
    assert router.choose()["agent"] == "vps"


def test_weights_and_unroutable(tmp_path):
    g = make_genome(tmp_path, """
        cheap:
          adapter: scripted
          cost: 0
        pricey:
          adapter: scripted
          cost: 1.5
        manual:
          adapter: scripted
          route: false
    """, extra="router:\n  weights: {success: 0, latency: 0, cost: 100, energy: 0}")
    k = Kernel(tmp_path / "k.db")
    router = Router(g, k)
    choice = router.choose()
    assert choice["agent"] == "cheap"
    assert {r["agent"] for r in choice["ranking"]} == {"cheap", "pricey"}
    assert router.choose(candidates=["manual"])["agent"] == "manual"
    assert router.choose(candidates=[])["agent"] is None
