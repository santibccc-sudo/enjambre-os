"""Router: the best agent for a task right now, with a reason a human can read.

Scores mix measured fitness (success rate, median latency) with declared facts
(cost, energy source). Agents without data get neutral priors, never zeros.

    router:
      weights: {success: 35, latency: 15, cost: 20, energy: 30}
      solar_window: {start: "09:30", end: "17:30", timezone: "Europe/Madrid"}

Agents with `energy: solar` score high while the sun is up and low after dark,
so work drifts towards free, clean compute when it exists.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from datetime import time as dtime
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_WEIGHTS = {"success": 35.0, "latency": 15.0, "cost": 20.0, "energy": 30.0}
PRIOR_SUCCESS = 70.0
PRIOR_LATENCY = 50.0


def _hm(value: str) -> dtime:
    hours, minutes = str(value).split(":")
    return dtime(int(hours), int(minutes))


def _zone(name: str):
    if str(name).upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        # Windows without the tzdata package knows no zones at all.
        return timezone.utc


class Router:
    def __init__(self, genome, kernel, *, worker_prefix: str = "scheduler", clock: Callable[[], float] | None = None):
        self.genome = genome
        self.kernel = kernel
        self.prefix = worker_prefix
        self._clock = clock or time.time

    def weights(self) -> dict[str, float]:
        weights = dict(DEFAULT_WEIGHTS)
        for key, value in (self.genome.router.get("weights") or {}).items():
            if key in weights:
                weights[key] = max(0.0, float(value))
        return weights

    def solar_active(self, at: float | None = None) -> bool:
        window = self.genome.router.get("solar_window")
        if not window:
            return False
        now = datetime.fromtimestamp(self._clock() if at is None else at, _zone(window.get("timezone", "UTC")))
        start, end = _hm(window.get("start", "10:00")), _hm(window.get("end", "17:00"))
        t = now.time()
        return start <= t < end if start <= end else (t >= start or t < end)

    def score(self, spec, fitness: dict, solar: bool) -> dict:
        f = fitness.get(f"{self.prefix}/{spec.id}") or fitness.get(spec.id) or {}
        measured = bool(f.get("runs"))
        success = f["success_rate"] * 100 if measured and f.get("success_rate") is not None else PRIOR_SUCCESS
        latency = max(0.0, 100.0 - f["median_ms"] / 3000) if measured and f.get("median_ms") is not None else PRIOR_LATENCY
        cost = 100.0 if spec.cost <= 0 else max(0.0, 100.0 - spec.cost * 50)
        energy = {"solar": 100.0 if solar else 25.0, "always-on": 60.0, "grid": 40.0}[spec.energy]
        parts = {"success": success, "latency": latency, "cost": cost, "energy": energy}
        weights = self.weights()
        total = sum(parts[k] * weights[k] for k in parts) / (sum(weights.values()) or 1.0)
        return {"agent": spec.id, "score": round(total, 1), "parts": {k: round(v, 1) for k, v in parts.items()},
                "measured": measured}

    def choose(self, candidates: list[str] | None = None) -> dict:
        agents = self.genome.agents
        specs = [a for a in agents.values() if a.route] if candidates is None else [agents[c] for c in candidates if c in agents]
        solar = self.solar_active()
        fitness = self.kernel.fitness()
        ranking = sorted((self.score(s, fitness, solar) for s in specs), key=lambda r: (-r["score"], r["agent"]))
        if not ranking:
            return {"agent": None, "solar": solar, "ranking": [], "reason": "no routable agents"}
        best = ranking[0]
        evidence = "measured" if best["measured"] else "no data yet"
        sky = "solar window open" if solar else "no sun"
        return {"agent": best["agent"], "solar": solar, "ranking": ranking,
                "reason": f"router: {sky} -> {best['agent']} (score {best['score']}, {evidence})"}
