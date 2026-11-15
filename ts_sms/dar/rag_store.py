"""
RAG data hierarchy store backing the DAR data scope:
family -> parent -> children -> assignment -> rag_source -> rag_chunk -> rag_fact.

Governance is authoritative at ``rag_source``; ``rag_chunk`` and ``rag_fact`` carry
lineage only and inherit their mother source's decision. Loaded from the JSON file
named by ``TS_SMS_RAG_STORE_PATH``::

    {"rag_sources": [{
        "rag_source_id": "src_policy_2026", "classification": "CONFIDENTIAL",
        "visible_roles": ["analyst"], "rbac_ledger_confirmed": true, "legal_hold": false,
        "effective_from": "2026-01-01T00:00:00Z", "effective_to": null,
        "retention_expiry": "2031-01-01T00:00:00Z"
     }],
     "rag_facts": [{
        "rag_fact_id": "fact_supervisor_limit", "rag_source_id": "src_policy_2026",
        "fact_type": "threshold", "key": "supervisor_limit", "value": {"amount": 500}
     }]}

rag_fact is optional: no configured store means no sources or facts, never a failure.
Fail-closed: a source missing any governance field, or failing any check, is DENY;
a fact whose source is unknown or denied is dropped.
"""

import json
from typing import Any, Dict, List, Optional

from ts_sms.dar.assignments import _parse_time

RAG_FACT_TYPES = frozenset({"threshold", "time_standard", "role_policy", "override_rule", "glossary"})


class RagStoreError(ValueError):
    """The configured RAG store is unreadable or malformed (non-retryable)."""


def load_rag_store(path: Optional[str]) -> Dict[str, List[Dict[str, Any]]]:
    if not path:
        return {"rag_sources": [], "rag_facts": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise RagStoreError(f"RAG store unreadable: {type(e).__name__}") from None
    if not isinstance(data, dict):
        raise RagStoreError("RAG store must be a JSON object")
    store = {}
    for name in ("rag_sources", "rag_facts"):
        records = data.get(name, [])
        if not isinstance(records, list):
            raise RagStoreError(f"RAG store '{name}' must be a list")
        store[name] = records
    return store


def rag_source_effect(source: Any, role: str, classification: str, as_of: float) -> str:
    """Source-level governance decision; any missing or failing attribute is DENY."""
    try:
        if source.get("rbac_ledger_confirmed") is not True or source.get("legal_hold") is not False:
            return "DENY"
        if source.get("classification") != classification:
            return "DENY"
        if role not in (source.get("visible_roles") or ()):
            return "DENY"
        start = _parse_time(source["effective_from"])
        # effective_to must be declared; an explicit null means open-ended (still bounded by retention_expiry).
        end = None if source["effective_to"] is None else _parse_time(source["effective_to"])
        # retention_expiry is derived from the source's 5-year / 7-year retention class.
        retention_expiry = _parse_time(source["retention_expiry"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return "DENY"
    if as_of < start or (end is not None and as_of >= end) or as_of >= retention_expiry:
        return "DENY"
    return "ALLOW"


def inherit_source_effect(rag_source_id: Any, source_effects: Dict[Any, str]) -> str:
    """Derived records (rag_chunk, rag_fact) inherit their mother rag_source's decision; unresolvable => DENY."""
    if rag_source_id is None:
        return "DENY"
    return source_effects.get(rag_source_id, "DENY")


def to_rag_fact(record: Any) -> Optional[Dict[str, Any]]:
    """
    Projects a rag_fact record onto identity/linkage/lookup fields only.
    Fact-level effective_from/effective_to/visibility/retention (and legacy
    family_id/child_id) are dropped: governance derives from rag_source.
    Accepts rag_fact_id/rag_source_id, falling back to legacy id/source_id;
    conflicting values are rejected.
    """
    if not isinstance(record, dict):
        return None
    fact_id = _canonical_field(record, "rag_fact_id", "id")
    source_id = _canonical_field(record, "rag_source_id", "source_id")
    fact_type = record.get("fact_type")
    key = record.get("key")
    if not isinstance(fact_id, str) or not fact_id or not isinstance(source_id, str) or not source_id:
        return None
    if not isinstance(key, str) or not key or fact_type not in RAG_FACT_TYPES:
        return None
    try:
        value = json.loads(json.dumps(record.get("value"), sort_keys=True, allow_nan=False))
    except (TypeError, ValueError):
        return None
    return {"rag_fact_id": fact_id, "rag_source_id": source_id, "fact_type": fact_type, "key": key, "value": value}


def _canonical_field(record: Dict[str, Any], name: str, legacy_name: str) -> Any:
    value = record.get(name)
    legacy = record.get(legacy_name)
    if value is not None and legacy is not None and value != legacy:
        return None
    return value if value is not None else legacy


def resolve_rag_scope(store: Dict[str, List[Dict[str, Any]]], role: str, classification: str,
                      as_of: float) -> Dict[str, Any]:
    source_effects: Dict[str, str] = {}
    for source in store["rag_sources"]:
        sid = source.get("rag_source_id") if isinstance(source, dict) else None
        if not isinstance(sid, str) or not sid:
            continue
        effect = rag_source_effect(source, role, classification, as_of)
        # Duplicate ids fail closed: any DENY for an id wins.
        source_effects[sid] = "DENY" if source_effects.get(sid) == "DENY" else effect
    eligible = sorted(sid for sid, effect in source_effects.items() if effect == "ALLOW")

    facts = []
    for record in store["rag_facts"]:
        fact = to_rag_fact(record)
        if fact is not None and inherit_source_effect(fact["rag_source_id"], source_effects) == "ALLOW":
            facts.append(fact)
    facts.sort(key=lambda f: (f["rag_source_id"], f["fact_type"], f["key"], f["rag_fact_id"]))
    return {"eligible_rag_source_ids": eligible, "rag_facts": facts}
