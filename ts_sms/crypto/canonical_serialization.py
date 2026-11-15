import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Deterministic JSON encoding (sorted keys, no whitespace, no NaN) used for every hashed input."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def legacy_canonical_json(value: Any) -> str:
    """v1 ledger encoding (``json.dumps(sort_keys=True)``); kept only to verify pre-v2 entries."""
    return json.dumps(value, sort_keys=True)
