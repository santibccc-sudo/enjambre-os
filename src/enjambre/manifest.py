"""Validate agent manifests against schemas/agent-manifest.schema.json.

A manifest is one agent entry from `agents:` in a swarm.yaml genome, with the
map key promoted to an 'id' field so it stands alone as a shareable document
(see tests/fixtures/manifests/*.json and `enjambre validate` in cli.py). This
is the runtime counterpart of tests/test_manifest_schema.py: same bundled
schema, usable outside pytest and after a plain `pip install enjambre`.

`jsonschema` is only required if you actually validate something (extra:
`pip install 'enjambre[validate]'`); the rest of enjambre does not need it.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).parent / "schemas" / "agent-manifest.schema.json"


class ManifestError(ValueError):
    pass


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _validator():
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise ManifestError(
            "validating manifests needs the 'jsonschema' package: pip install 'enjambre[validate]'"
        ) from exc
    return Draft202012Validator(load_schema())


def errors(manifest: dict[str, Any]) -> list[str]:
    """Human-readable validation errors, in path order; empty if the manifest is valid."""
    found = sorted(_validator().iter_errors(manifest), key=lambda e: [str(p) for p in e.path])
    return [f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in found]


def validate(manifest: dict[str, Any]) -> None:
    """Raise ManifestError listing every problem if the manifest doesn't match the schema."""
    problems = errors(manifest)
    if problems:
        raise ManifestError("\n".join(problems))


def validate_file(path: str | Path) -> list[str]:
    """Load a JSON manifest file and return its validation errors (empty if valid)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return ["(root): manifest must be a JSON object"]
    return errors(data)
