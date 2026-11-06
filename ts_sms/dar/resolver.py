from typing import Any, Dict

from temporalio import activity

from ts_sms.crypto.hash_derivation import sha256_hex


@activity.defn
def resolve_dual_authority(request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executes the Dual-Hierarchy DAR algorithm.
    Resolves both Organizational Scope (Tenant -> Family -> Child)
    and Data Scope (Document Collections).
    """
    user_id = request.get("user_id")
    tenant_id = request.get("tenant_id")

    # 1. Org Scope Resolution (ReBAC validation)
    org_scope = _resolve_org_scope(user_id, tenant_id)
    if not org_scope:
        return {"effect": "DENY", "reason": "No Valid Organizational Assignment"}

    # 2. Data Scope Resolution
    data_scope = _resolve_data_scope(org_scope)

    # 3. Intersection & Hash Derivation (TS-CONTRACT-002)
    gov_context_string = f"{org_scope['id']}:{data_scope['classification']}:{request['timestamp']}"
    context_hash = sha256_hex(gov_context_string)

    return {
        "effect": "ALLOW",
        "org_scope": org_scope,
        "data_scope": data_scope,
        "context_hash": context_hash,
    }


def _resolve_org_scope(user_id: str, tenant_id: str) -> dict:
    # Stub: Query strictly isolated Relational DB for temporal Assignment bounds
    # Returns Child node ID if assignment is active.
    if not user_id or not tenant_id:
        return {}
    return {"id": f"child_node_{tenant_id}", "level": "Child"}


def _resolve_data_scope(org_scope: dict) -> dict:
    # Stub: Query strictly isolated Data Classification DB
    return {"classification": "CONFIDENTIAL", "retention_status": "ACTIVE"}
