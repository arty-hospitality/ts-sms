from typing import Any, Dict, Optional

from ts_sms.config import Settings
from ts_sms.crypto.canonical_serialization import canonical_json
from ts_sms.crypto.hash_derivation import sha256_hex
from ts_sms.dar.assignments import find_active_assignment, load_assignments
from ts_sms.dar.rag_store import load_rag_store, resolve_rag_scope


def _deny(reason: str) -> Dict[str, Any]:
    return {"effect": "DENY", "reason": reason}


def resolve_dual_authority(request: Dict[str, Any], settings: Optional[Settings] = None) -> Dict[str, Any]:
    """
    Executes the Dual-Hierarchy DAR algorithm.
    Resolves both Organizational Scope (Tenant -> Family -> Child)
    and Data Scope (Document Collections) from an active, time-bounded
    assignment. ``evaluated_at`` is set by the workflow, never by the caller.
    """
    user_id = request.get("user_id")
    tenant_id = request.get("tenant_id")
    as_of = request.get("evaluated_at")
    if not isinstance(user_id, str) or not user_id or not isinstance(tenant_id, str) or not tenant_id:
        return _deny("Missing principal or tenant")
    if isinstance(as_of, bool) or not isinstance(as_of, (int, float)):
        return _deny("Missing authoritative evaluation time")

    settings = settings or Settings()
    records = load_assignments(settings.assignments_path)
    assignment = find_active_assignment(records, user_id, tenant_id, float(as_of))
    if assignment is None:
        return _deny("No Valid Organizational Assignment")

    org_scope = _resolve_org_scope(assignment)
    data_scope = _resolve_data_scope(assignment, float(as_of), settings)

    # TS-CONTRACT-002: governance context bound to principal, tenant, scopes and evaluation time
    # (data_scope carries the eligible rag_source ids and the rag_fact -> rag_source lineage)
    context_hash = sha256_hex(canonical_json({
        "user_id": user_id,
        "tenant_id": tenant_id,
        "org_scope": org_scope,
        "data_scope": data_scope,
        "evaluated_at": as_of,
    }))

    return {
        "effect": "ALLOW",
        "org_scope": org_scope,
        "data_scope": data_scope,
        "context_hash": context_hash,
    }


def _resolve_org_scope(assignment: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": assignment["org_node_id"],
        "level": assignment["level"],
        "tenant_id": assignment["tenant_id"],
        "role": assignment["role"],
    }


def _resolve_data_scope(assignment: Dict[str, Any], as_of: float, settings: Settings) -> Dict[str, Any]:
    classification = assignment["data_classification"]
    rag_scope = resolve_rag_scope(load_rag_store(settings.rag_store_path), {
        "tenant_id": assignment["tenant_id"],
        "org_node_id": assignment["org_node_id"],
        "role": assignment["role"],
        "classification": classification,
    }, as_of)
    return {"classification": classification, "retention_status": "ACTIVE", **rag_scope}
