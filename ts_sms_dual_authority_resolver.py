import hashlib
import json
from typing import Dict, Any, List, Optional

# Data hierarchy: family -> parent -> children -> assignment -> rag_source -> rag_chunk -> rag_fact.
# Governance is authoritative at rag_source; rag_chunk and rag_fact carry lineage only.
RAG_FACT_TYPES = frozenset({"threshold", "time_standard", "role_policy", "override_rule", "glossary"})

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
    data_scope = _resolve_data_scope(org_scope, request)
    
    # 3. Intersection & Hash Derivation (TS-CONTRACT-002)
    gov_context_string = (
        f"{org_scope['id']}:{data_scope['classification']}:{request['timestamp']}"
        f":{_rag_lineage_digest(data_scope)}"
    )
    context_hash = hashlib.sha256(gov_context_string.encode('utf-8')).hexdigest()

    return {
        "effect": "ALLOW",
        "org_scope": org_scope,
        "data_scope": data_scope,
        "context_hash": context_hash
    }

def _resolve_org_scope(user_id: str, tenant_id: str) -> dict:
    # Stub: Query strictly isolated Relational DB for temporal Assignment bounds
    # Returns Child node ID if assignment is active.
    return {"id": f"child_node_{tenant_id}", "level": "Child"}

def _resolve_data_scope(org_scope: dict, request: Dict[str, Any]) -> dict:
    # Stub: Query strictly isolated Data Classification DB
    as_of = request.get("timestamp")
    role = org_scope.get("role")

    classification = "CONFIDENTIAL"
    source_effects = {
        source["rag_source_id"]: _rag_source_effect(source, role, as_of, classification)
        for source in _load_rag_sources(org_scope)
        if source.get("rag_source_id") is not None
    }
    eligible_rag_source_ids = sorted(
        (sid for sid, effect in source_effects.items() if effect == "ALLOW"), key=str
    )

    # rag_fact is optional: an empty result supplements nothing and never fails the flow.
    rag_facts = []
    for record in _load_rag_facts(org_scope):
        fact = _to_rag_fact(record)
        if fact is not None and _inherit_source_effect(fact["rag_source_id"], source_effects) == "ALLOW":
            rag_facts.append(fact)
    rag_facts.sort(key=lambda f: (str(f["rag_source_id"]), f["fact_type"], str(f["key"]), str(f["rag_fact_id"])))

    return {
        "classification": classification,
        "retention_status": "ACTIVE",
        "eligible_rag_source_ids": eligible_rag_source_ids,
        "rag_facts": rag_facts,
    }

def _load_rag_sources(org_scope: dict) -> List[dict]:
    # Stub: rag_source records (with source-level governance) bound to the resolved assignment.
    return []

def _load_rag_facts(org_scope: dict) -> List[dict]:
    # Stub: optional rag_fact records linked to rag_source via source_id.
    return []

def _rag_source_effect(source: dict, role: Optional[str], as_of: Any, classification: str) -> str:
    """Source-level governance decision; any missing or failing attribute is DENY (fail-closed)."""
    try:
        if as_of is None or role is None:
            return "DENY"
        # Mirror the EEB document constraints so facts never outlive what document filters exclude.
        if source.get("legal_hold") is not False:
            return "DENY"
        if source.get("classification") != classification:
            return "DENY"
        if source.get("rbac_ledger_confirmed") is not True:
            return "DENY"
        if role not in (source.get("visible_roles") or ()):
            return "DENY"
        effective_from = source["effective_from"]
        # effective_to must be declared; an explicit None means open-ended (still bounded by retention_expiry).
        effective_to = source["effective_to"]
        if as_of < effective_from or (effective_to is not None and as_of >= effective_to):
            return "DENY"
        # retention_expiry is derived from the source's 5-year / 7-year retention class.
        if as_of >= source["retention_expiry"]:
            return "DENY"
        return "ALLOW"
    except (KeyError, TypeError):
        return "DENY"

def _inherit_source_effect(rag_source_id: Any, source_effects: Dict[Any, str]) -> str:
    """Derived records (rag_chunk, rag_fact) inherit their mother rag_source's decision; unresolvable => DENY."""
    if rag_source_id is None:
        return "DENY"
    return source_effects.get(rag_source_id, "DENY")

def _to_rag_fact(record: dict) -> Optional[dict]:
    """
    Projects a rag_fact record onto identity/linkage/lookup fields only.
    Fact-level effective_from/effective_to/visibility/retention (and legacy
    family_id/child_id) are intentionally dropped: governance derives from rag_source.
    Accepts the documented rag_fact_id/rag_source_id names, falling back to legacy id/source_id;
    conflicting values are rejected.
    """
    fact_id = _canonical_field(record, "rag_fact_id", "id")
    source_id = _canonical_field(record, "rag_source_id", "source_id")
    fact_type = record.get("fact_type")
    key = record.get("key")
    if fact_id is None or source_id is None or key is None or fact_type not in RAG_FACT_TYPES:
        return None
    try:
        value = json.loads(json.dumps(record.get("value"), sort_keys=True))
    except (TypeError, ValueError):
        return None
    return {
        "rag_fact_id": fact_id,
        "rag_source_id": source_id,
        "fact_type": fact_type,
        "key": key,
        "value": value,
    }

def _canonical_field(record: dict, name: str, legacy_name: str) -> Any:
    value = record.get(name)
    legacy = record.get(legacy_name)
    if value is not None and legacy is not None and value != legacy:
        return None
    return value if value is not None else legacy

def _rag_lineage_digest(data_scope: dict) -> str:
    lineage = {
        "eligible_rag_source_ids": data_scope["eligible_rag_source_ids"],
        "rag_fact_lineage": [[f["rag_fact_id"], f["rag_source_id"]] for f in data_scope["rag_facts"]],
    }
    canonical = json.dumps(lineage, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
