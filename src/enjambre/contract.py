"""The result contract every agent adapter returns.

The model may write the content, but it never decides by prose whether a run
finished: the adapter reports a technical status and the kernel persists it.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

STATUSES = ("completed", "failed", "cancelled")

_ALIASES = {
    "ok": "completed", "success": "completed", "succeeded": "completed", "done": "completed",
    "error": "failed", "failure": "failed",
    "canceled": "cancelled",
}

OUTPUT_CONTRACT = """
OUTPUT CONTRACT (mandatory):
Reply with a single JSON object and nothing else:
{"status": "completed|failed|cancelled",
 "result": "what you delivered, for a human",
 "error": "why it failed (empty if completed)",
 "error_code": "short machine code (optional)",
 "artifacts": [{"path": "/absolute/path/or/https://url", "kind": "file|url"}]}
Use "completed" only if the work is really finished. Declare every file or URL you
produced in "artifacts": the kernel checks that they exist before trusting you.
""".strip()


def _clip(value: Any, limit: int) -> str:
    return str(value if value is not None else "")[:limit]


def _dict_list(value: Any, limit: int = 64) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value[:limit]:
        if isinstance(item, dict):
            out.append(dict(item))
        elif item is not None:
            out.append({"value": str(item)})
    return out


@dataclass
class AgentResult:
    status: str
    result: str = ""
    error: str = ""
    error_code: str = ""
    tools: list[dict] = field(default_factory=list)
    artifacts: list[dict] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"invalid terminal status: {self.status!r}")
        self.result = _clip(self.result, 24_000)
        self.error = _clip(self.error, 4_000)
        self.error_code = _clip(self.error_code, 120)
        self.tools = _dict_list(self.tools)
        self.artifacts = _dict_list(self.artifacts)
        self.meta = dict(self.meta) if isinstance(self.meta, dict) else {}

    @property
    def ok(self) -> bool:
        return self.status == "completed"

    @classmethod
    def completed(cls, result: str = "", **kw: Any) -> "AgentResult":
        return cls(status="completed", result=result, **kw)

    @classmethod
    def failed(cls, error: str, error_code: str = "", **kw: Any) -> "AgentResult":
        return cls(status="failed", error=error, error_code=error_code, **kw)

    def visible_text(self) -> str:
        if self.status == "completed":
            return self.result or "(completed without output)"
        if self.status == "cancelled":
            return f"cancelled: {self.error or 'cancelled by the system'}"
        header = f"failed: {self.error or 'the adapter failed without detail'}"
        body = (self.result or "").strip()
        # The diagnosis travels after the header, never instead of it.
        return header if not body or body == self.error.strip() else f"{header}\n\n{body}"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_any(cls, value: Any) -> "AgentResult":
        """Normalise what an adapter or remote client sent. Never guesses from prose."""
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            status = str(value.get("status") or "").strip().lower()
            status = _ALIASES.get(status, status)
            if status not in STATUSES:
                return cls.failed(
                    f"result declared an invalid status: {value.get('status')!r}",
                    "contract_invalid_status",
                    result=_clip(value.get("result"), 2_000),
                )
            return cls(
                status=status,
                result=value.get("result", ""),
                error=value.get("error", ""),
                error_code=value.get("error_code", ""),
                tools=value.get("tools", []),
                artifacts=value.get("artifacts", []),
                meta=value.get("meta", {}),
            )
        if isinstance(value, str) and value.strip():
            return cls.completed(value.strip(), meta={"plain_text": True})
        return cls.failed("the adapter returned no result", "empty_result")

    @classmethod
    def parse(cls, text: str) -> "AgentResult":
        """Extract the JSON contract from a model reply (tolerates fences and chatter)."""
        obj = _find_contract_object(text or "")
        if obj is None:
            return cls.failed(
                "the agent did not return the JSON output contract",
                "contract_invalid_json",
                result=_clip(text, 2_000),
            )
        return cls.from_any(obj)


def _find_contract_object(text: str) -> dict | None:
    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    idx = text.find("{")
    while idx != -1:
        try:
            obj, end = decoder.raw_decode(text, idx)
        except ValueError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict) and "status" in obj:
            candidates.append(obj)
        idx = text.find("{", end)
    # The last object wins: models often think out loud before the final answer.
    return candidates[-1] if candidates else None
