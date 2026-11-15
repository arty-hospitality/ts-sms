from typing import Any, Dict, List

FACT_FIELDS = ("rag_fact_id", "rag_source_id", "fact_type", "key", "value")

def build_fact_query(eeb: Dict[str, Any], size: int = 10) -> Dict[str, Any]:
    """
    Translates the EEB's self-contained rag_fact boundary into an OpenSearch/Elasticsearch query.
    Only filters["rag_fact"]["must"] is applied; the top-level must/must_not bound the document/chunk index.
    Fails closed if the boundary does not confine facts to eligible rag_source ids.
    """
    if not eeb or not eeb.get("eeb_hash"):
        raise ValueError("EEB required before fact retrieval (Law 10)")
    must = eeb["filters"]["rag_fact"]["must"]
    if not any(isinstance(c, dict) and isinstance(c.get("terms", {}).get("rag_source_id"), list) for c in must):
        raise ValueError("rag_fact boundary must confine facts to eligible rag_source ids")
    return {
        "size": size,
        "query": {"bool": {"filter": must}},
        "_source": list(FACT_FIELDS),
        "sort": [{"rag_source_id": "asc"}, {"fact_type": "asc"}, {"key": "asc"}, {"rag_fact_id": "asc"}],
    }

def search_facts(client: Any, index: str, eeb: Dict[str, Any], size: int = 10) -> List[Dict[str, Any]]:
    """Runs a fact lookup confined by the EEB. `client` is an opensearch-py / elasticsearch-py compatible client."""
    response = client.search(index=index, body=build_fact_query(eeb, size))
    return [{f: hit["_source"].get(f) for f in FACT_FIELDS} for hit in response["hits"]["hits"]]

FACT_INDEX_MAPPING = {
    "mappings": {
        "dynamic": "strict",
        "properties": {
            "rag_fact_id": {"type": "keyword"},
            "rag_source_id": {"type": "keyword"},
            "fact_type": {"type": "keyword"},
            "key": {"type": "keyword"},
            "value": {"type": "object", "enabled": False},
        },
    }
}
