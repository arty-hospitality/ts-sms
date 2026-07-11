from typing import Dict, Any
import json
import hashlib

def build_evidence_boundary(auth_context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Synthesizes the DAR output into an executable, deterministic query boundary (EEB).
    This boundary strictly confines the downstream Vector DB / TrustRAG engine.
    """
    
    # Extract resolved scopes
    org_scope = auth_context["org_scope"]
    data_scope = auth_context["data_scope"]
    
    # Construct strictly parameterized filters (Preventing injection)
    eeb_filters = {
        "must": [
            {"term": {"org_node_id": org_scope["id"]}},
            {"term": {"classification": data_scope["classification"]}}
        ],
        "must_not": [
            {"term": {"legal_hold": True}},
            {"range": {"retention_expiry": {"lt": "now"}}} # Enforce retention laws
        ]
    }
    
    # Canonical Serialization for Ledger Integrity
    eeb_canonical = json.dumps(eeb_filters, sort_keys=True)
    eeb_hash = hashlib.sha256(eeb_canonical.encode('utf-8')).hexdigest()
    
    return {
        "filters": eeb_filters,
        "eeb_hash": eeb_hash,
        "auth_context_hash": auth_context["context_hash"]
    }