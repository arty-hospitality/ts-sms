import os
import uuid

import pytest

import ts_sms_dual_authority_resolver as dar
from ts_sms_evidence_enforcement_boundary import build_evidence_boundary
from ts_sms_fact_index_adapter import FACT_INDEX_MAPPING, search_facts

opensearchpy = pytest.importorskip("opensearchpy")

pytestmark = pytest.mark.integration

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")
REQUEST = {"user_id": "user_1", "tenant_id": "tenant_A", "timestamp": 100}
GOVERNED = dict(
    rbac_ledger_confirmed=True, visible_roles=["supervisor"], effective_from=50, effective_to=None,
    retention_expiry=500, legal_hold=False, classification="CONFIDENTIAL",
)
SOURCES = {
    "src_ok": dict(GOVERNED),
    "src_legal_hold": dict(GOVERNED, legal_hold=True),
    "src_expired": dict(GOVERNED, retention_expiry=90),
    "src_wrong_role": dict(GOVERNED, visible_roles=["staff"]),
}
FACTS = [
    {"rag_fact_id": "f_ok_threshold", "rag_source_id": "src_ok", "fact_type": "threshold", "key": "supervisor_limit", "value": {"amount": 500}},
    {"rag_fact_id": "f_ok_glossary", "rag_source_id": "src_ok", "fact_type": "glossary", "key": "supervisor_limit", "value": "Max approval"},
    {"rag_fact_id": "f_hold", "rag_source_id": "src_legal_hold", "fact_type": "threshold", "key": "supervisor_limit", "value": {"amount": 1}},
    {"rag_fact_id": "f_expired", "rag_source_id": "src_expired", "fact_type": "threshold", "key": "supervisor_limit", "value": {"amount": 2}},
    {"rag_fact_id": "f_role", "rag_source_id": "src_wrong_role", "fact_type": "threshold", "key": "supervisor_limit", "value": {"amount": 3}},
    {"rag_fact_id": "f_orphan", "rag_source_id": "src_unknown", "fact_type": "threshold", "key": "supervisor_limit", "value": {"amount": 4}},
]

@pytest.fixture(scope="module")
def fact_index():
    client = opensearchpy.OpenSearch(OPENSEARCH_URL)
    try:
        client.info()
    except Exception as exc:
        if os.environ.get("REQUIRE_OPENSEARCH") == "1":
            raise
        pytest.skip(f"OpenSearch not reachable at {OPENSEARCH_URL}: {exc}")
    index = f"rag_fact_test_{uuid.uuid4().hex}"
    client.indices.create(index=index, body=FACT_INDEX_MAPPING)
    for fact in FACTS:
        client.index(index=index, id=fact["rag_fact_id"], body=fact)
    client.indices.refresh(index=index)
    yield client, index
    client.indices.delete(index=index)

def _eeb(monkeypatch, sources, fact_lookup=None):
    monkeypatch.setattr(dar, "_resolve_org_scope", lambda u, t: {"id": f"child_node_{t}", "level": "Child", "role": "supervisor"})
    monkeypatch.setattr(dar, "_load_rag_sources", lambda o: [dict(s, rag_source_id=sid) for sid, s in sources.items()])
    monkeypatch.setattr(dar, "_load_rag_facts", lambda o: FACTS)
    auth_context = dar.resolve_dual_authority(REQUEST)
    return build_evidence_boundary(auth_context, fact_lookup)

def _ids(facts):
    return sorted(f["rag_fact_id"] for f in facts)

def test_only_facts_of_eligible_sources_are_returned(fact_index, monkeypatch):
    client, index = fact_index
    eeb = _eeb(monkeypatch, SOURCES)
    assert eeb["filters"]["rag_fact"]["must"] == [{"terms": {"rag_source_id": ["src_ok"]}}]
    assert _ids(search_facts(client, index, eeb)) == ["f_ok_glossary", "f_ok_threshold"]

def test_precise_lookup_narrows_within_eligible_sources(fact_index, monkeypatch):
    client, index = fact_index
    eeb = _eeb(monkeypatch, SOURCES, {"fact_type": "threshold", "key": "supervisor_limit"})
    assert search_facts(client, index, eeb) == [
        {"rag_fact_id": "f_ok_threshold", "rag_source_id": "src_ok", "fact_type": "threshold",
         "key": "supervisor_limit", "value": {"amount": 500}}
    ]

def test_lookup_cannot_reach_ineligible_source_facts(fact_index, monkeypatch):
    client, index = fact_index
    eeb = _eeb(monkeypatch, {k: v for k, v in SOURCES.items() if k != "src_ok"}, {"fact_type": "threshold", "key": "supervisor_limit"})
    assert search_facts(client, index, eeb) == []

def test_empty_eligible_sources_matches_no_facts(fact_index, monkeypatch):
    client, index = fact_index
    eeb = _eeb(monkeypatch, {})
    assert eeb["filters"]["rag_fact"]["must"] == [{"terms": {"rag_source_id": []}}]
    assert search_facts(client, index, eeb) == []

def test_missing_source_confinement_fails_closed(fact_index, monkeypatch):
    client, index = fact_index
    eeb = _eeb(monkeypatch, SOURCES)
    eeb["filters"]["rag_fact"]["must"] = [{"term": {"key": "supervisor_limit"}}]
    with pytest.raises(ValueError):
        search_facts(client, index, eeb)
