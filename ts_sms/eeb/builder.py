from typing import Any, Dict, List, Optional

from temporalio import activity

from ts_sms.crypto.canonical_serialization import canonical_json
from ts_sms.crypto.hash_derivation import sha256_hex
from ts_sms.dar.rag_store import RAG_FACT_TYPES


@activity.defn
def build_evidence_boundary(auth_context: Dict[str, Any], fact_lookup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Synthesizes the DAR output into an executable, deterministic query boundary (EEB).
    This boundary strictly confines the downstream Vector DB / TrustRAG engine.

    Filter contract:
      - top-level "must" / "must_not": the complete boundary for the document / rag_chunk index.
      - "rag_fact": the complete, self-contained boundary for the rag_fact index; adapters apply
        filters["rag_fact"]["must"] to fact searches. Source-level governance (temporal window, role,
        RBAC-in-ledger, retention, legal hold, classification) is already resolved by the DAR into
        eligible_rag_source_ids, so facts are confined by source membership only.
    """

    # Extract resolved scopes
    org_scope = auth_context["org_scope"]
    data_scope = auth_context["data_scope"]

    # Construct strictly parameterized filters (Preventing injection)
    eeb_filters = {
        "must": [
            {"term": {"tenant_id": org_scope["tenant_id"]}},
            {"term": {"org_node_id": org_scope["id"]}},
            {"term": {"classification": data_scope["classification"]}},
        ],
        "must_not": [
            {"term": {"legal_hold": True}},
            {"range": {"retention_expiry": {"lt": "now"}}},  # Enforce retention laws
        ],
        # rag_fact inherits governance from its mother rag_source: no fact-level
        # temporal/visibility/retention clauses, only eligible-source membership.
        "rag_fact": {
            "must": [{"terms": {"rag_source_id": sorted(data_scope.get("eligible_rag_source_ids", []))}}]
            + _fact_lookup_filters(fact_lookup)
        },
    }

    # Canonical Serialization for Ledger Integrity
    eeb_hash = sha256_hex(canonical_json(eeb_filters))

    return {
        "filters": eeb_filters,
        "eeb_hash": eeb_hash,
        "auth_context_hash": auth_context["context_hash"],
    }


def _fact_lookup_filters(fact_lookup: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Optional precise key/fact_type lookup; supplements, never replaces, chunk retrieval."""
    if not fact_lookup:
        return []
    if not isinstance(fact_lookup, dict):
        raise ValueError("rag_fact lookup must be an object")
    unsupported = set(fact_lookup) - {"fact_type", "key"}
    if unsupported:
        raise ValueError(f"Unsupported rag_fact lookup fields: {sorted(unsupported)}")
    filters = []
    fact_type = fact_lookup.get("fact_type")
    if fact_type is not None:
        if fact_type not in RAG_FACT_TYPES:
            raise ValueError("Unsupported rag_fact fact_type")
        filters.append({"term": {"fact_type": fact_type}})
    key = fact_lookup.get("key")
    if key is not None:
        if not isinstance(key, str) or not key:
            raise ValueError("rag_fact key must be a non-empty string")
        filters.append({"term": {"key": key}})
    return filters
