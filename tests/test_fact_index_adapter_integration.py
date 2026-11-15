import json
import os
import uuid

import pytest

from tests.test_components import REQUEST
from tests.test_rag_fact import SOURCE
from ts_sms.dar.resolver import resolve_dual_authority
from ts_sms.eeb.builder import build_evidence_boundary
from ts_sms.retrieval.fact_index import FACT_INDEX_MAPPING, search_facts

opensearchpy = pytest.importorskip("opensearchpy")

pytestmark = pytest.mark.integration

OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "http://localhost:9200")
SOURCES = {
    "src_ok": {},
    "src_legal_hold": {"legal_hold": True},
    "src_expired": {"retention_expiry": "2026-01-02T00:00:00Z"},
    "src_wrong_role": {"visible_roles": ["staff"]},
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


def _eeb(tmp_path, monkeypatch, sources, fact_lookup=None):
    path = tmp_path / "rag.json"
    path.write_text(json.dumps({
        "rag_sources": [{**SOURCE, **override, "rag_source_id": sid} for sid, override in sources.items()],
        "rag_facts": FACTS,
    }))
    monkeypatch.setenv("TS_SMS_RAG_STORE_PATH", str(path))
    auth_context = resolve_dual_authority(REQUEST)
    assert auth_context["effect"] == "ALLOW"
    return build_evidence_boundary(auth_context, fact_lookup)


def _ids(facts):
    return sorted(f["rag_fact_id"] for f in facts)


def test_only_facts_of_eligible_sources_are_returned(fact_index, assignments, tmp_path, monkeypatch):
    client, index = fact_index
    eeb = _eeb(tmp_path, monkeypatch, SOURCES)
    assert eeb["filters"]["rag_fact"]["must"] == [{"terms": {"rag_source_id": ["src_ok"]}}]
    assert _ids(search_facts(client, index, eeb)) == ["f_ok_glossary", "f_ok_threshold"]


def test_precise_lookup_narrows_within_eligible_sources(fact_index, assignments, tmp_path, monkeypatch):
    client, index = fact_index
    eeb = _eeb(tmp_path, monkeypatch, SOURCES, {"fact_type": "threshold", "key": "supervisor_limit"})
    assert search_facts(client, index, eeb) == [
        {"rag_fact_id": "f_ok_threshold", "rag_source_id": "src_ok", "fact_type": "threshold",
         "key": "supervisor_limit", "value": {"amount": 500}}
    ]


def test_lookup_cannot_reach_ineligible_source_facts(fact_index, assignments, tmp_path, monkeypatch):
    client, index = fact_index
    sources = {k: v for k, v in SOURCES.items() if k != "src_ok"}
    eeb = _eeb(tmp_path, monkeypatch, sources, {"fact_type": "threshold", "key": "supervisor_limit"})
    assert search_facts(client, index, eeb) == []


def test_empty_eligible_sources_matches_no_facts(fact_index, assignments, tmp_path, monkeypatch):
    client, index = fact_index
    eeb = _eeb(tmp_path, monkeypatch, {})
    assert eeb["filters"]["rag_fact"]["must"] == [{"terms": {"rag_source_id": []}}]
    assert search_facts(client, index, eeb) == []


def test_missing_source_confinement_fails_closed(fact_index, assignments, tmp_path, monkeypatch):
    client, index = fact_index
    eeb = _eeb(tmp_path, monkeypatch, SOURCES)
    eeb["filters"]["rag_fact"]["must"] = [{"term": {"key": "supervisor_limit"}}]
    with pytest.raises(ValueError):
        search_facts(client, index, eeb)
