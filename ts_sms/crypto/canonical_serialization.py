import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Deterministic JSON encoding (sorted keys) used for every hashed input."""
    return json.dumps(value, sort_keys=True)
