import time

import pytest

from enjambre import Kernel


class FakeClock:
    """Starts at real time so file mtimes and kernel timestamps stay comparable."""

    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def kernel(tmp_path, clock):
    return Kernel(tmp_path / "swarm.db", clock=clock)
