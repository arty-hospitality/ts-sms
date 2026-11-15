"""
Time-bounded ReBAC assignment store backing the DAR.

Assignments are loaded from the JSON file named by ``TS_SMS_ASSIGNMENTS_PATH``::

    {"assignments": [{
        "user_id": "user_123", "tenant_id": "tenant_A",
        "org_node_id": "child_node_tenant_A", "level": "Child", "role": "analyst",
        "data_classification": "CONFIDENTIAL",
        "effective_from": "2026-01-01T00:00:00Z", "effective_to": null
    }]}

Fail-closed: no configured store, a record missing any required field, an
assignment outside ``effective_from <= as_of < effective_to`` or more than
one active assignment for the same (user, tenant) all resolve to no authority.
``effective_to`` must be declared; an explicit ``null`` means open-ended.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

REQUIRED_FIELDS = (
    "user_id", "tenant_id", "org_node_id", "level", "role",
    "data_classification", "effective_from", "effective_to",
)


class AssignmentStoreError(ValueError):
    """The configured assignment store is unreadable or malformed (non-retryable)."""


def _parse_time(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, str) or not value:
        raise ValueError("assignment time must be an ISO-8601 string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("assignment time must include a timezone")
    return dt.astimezone(timezone.utc).timestamp()


def load_assignments(path: Optional[str]) -> List[Dict[str, Any]]:
    if not path:
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise AssignmentStoreError(f"Assignment store unreadable: {type(e).__name__}") from None
    records = data.get("assignments") if isinstance(data, dict) else None
    if not isinstance(records, list):
        raise AssignmentStoreError("Assignment store must be an object with an 'assignments' list")
    return records


def _is_active(record: Any, user_id: str, tenant_id: str, as_of: float) -> bool:
    if not isinstance(record, dict) or any(k not in record for k in REQUIRED_FIELDS):
        return False
    if record["user_id"] != user_id or record["tenant_id"] != tenant_id:
        return False
    for k in ("org_node_id", "level", "role", "data_classification"):
        if not isinstance(record[k], str) or not record[k]:
            return False
    try:
        start = _parse_time(record["effective_from"])
        end = None if record["effective_to"] is None else _parse_time(record["effective_to"])
    except ValueError:
        return False
    return start <= as_of and (end is None or as_of < end)


def find_active_assignment(records: List[Dict[str, Any]], user_id: str, tenant_id: str,
                           as_of: float) -> Optional[Dict[str, Any]]:
    active = [r for r in records if _is_active(r, user_id, tenant_id, as_of)]
    if len(active) != 1:
        return None
    return active[0]
