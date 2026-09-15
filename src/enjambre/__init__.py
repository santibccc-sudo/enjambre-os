"""enjambre — a small, honest operating system for swarms of AI agents."""
from .contract import AgentResult
from .kernel import Kernel
from .policy import Policy

__version__ = "0.1.0"
__all__ = ["AgentResult", "Kernel", "Policy", "__version__"]
