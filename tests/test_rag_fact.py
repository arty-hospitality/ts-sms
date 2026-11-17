import json

import pytest

from tests.test_components import REQUEST
from ts_sms.crypto.canonical_serialization import canonical_json
from ts_sms.crypto.hash_derivation import sha256_hex
from ts_sms.dar.rag_store import RagStoreError
from ts_sms.dar.resolver import resolve_dual_authority
from ts_sms.eeb.builder import build_evidence_boundary

SOURCE = {
    "rag_source_id": "src_ok",
    "tenant_id": "tenant_A",
    "org_node_id": "child_node_tenant_A",
    "classification": "CONFIDENTIAL",
    "visible_roles": ["analyst"],
    "rbac_ledger_confirmed": True,
    "legal_hold": False,
    "effective_from": "2026-01-01T00:00:00Z",
    "effective_to": None,
    "retention_expiry": "2031-01-01T00:00:00Z",
}


def fact(fact_id, source_id, fact_type="threshold", key="supervisor_limit", **extra):
    return {"rag_fact_id": fact_id, "rag_source_id": source_id, "fact_type": fact_type, "key": key,
            "value": {"amount": 500}, **extra}


@pytest.fixture
def rag_store(tmp_path, monkeypatch, assignments):
    path = tmp_path / "rag.json"

    def write(sources, facts):
        path.write_text(json.dumps({"rag_sources": sources, "rag_facts": facts}))

    monkeypatch.setenv("TS_SMS_RAG_STORE_PATH", str(path))
    write([], [])
    return write


def scope():
    auth = resolve_dual_authority(REQUEST)
    assert auth["effect"] == "ALLOW"
    return auth["data_scope"]


def test_no_rag_store_is_optional(assignments):
    data_scope = scope()
    assert data_scope["eligible_rag_source_ids"] == [] and data_scope["rag_facts"] == []
    assert build_evidence_boundary(resolve_dual_authority(REQUEST))["filters"]["rag_fact"] == {
        "must": [{"terms": {"rag_source_id": []}}]}


def test_fact_projects_lineage_only_and_ignores_fact_level_governance(rag_store):
    rag_store([SOURCE], [fact("f1", "src_ok", effective_to="2000-01-01T00:00:00Z", visibility=[],
                              family_id="fam", child_id=None)])
    assert scope()["rag_facts"] == [{"rag_fact_id": "f1", "rag_source_id": "src_ok", "fact_type": "threshold",
                                     "key": "supervisor_limit", "value": {"amount": 500}}]


def test_legacy_field_names_accepted_and_conflicts_rejected(rag_store):
    rag_store([SOURCE], [
        {"id": "f_legacy", "source_id": "src_ok", "fact_type": "glossary", "key": "PAR", "value": "par level"},
        {**fact("f_conflict", "src_ok"), "source_id": "other"},
    ])
    assert [f["rag_fact_id"] for f in scope()["rag_facts"]] == ["f_legacy"]


@pytest.mark.parametrize("override", [
    {"legal_hold": True}, {"legal_hold": None}, {"classification": "INTERNAL"},
    {"rbac_ledger_confirmed": False}, {"visible_roles": ["supervisor"]},
    {"effective_from": "2027-01-01T00:00:00Z"}, {"effective_to": "2026-02-01T00:00:00Z"},
    {"retention_expiry": "2026-02-01T00:00:00Z"}, {"effective_to": "not-a-time"},
    {"tenant_id": "tenant_B"}, {"org_node_id": "child_node_other"}, {"visible_roles": "senior_analyst"},
])
def test_denied_source_denies_its_facts(rag_store, override):
    rag_store([{**SOURCE, **override}], [fact("f1", "src_ok")])
    data_scope = scope()
    assert data_scope["eligible_rag_source_ids"] == [] and data_scope["rag_facts"] == []


@pytest.mark.parametrize("missing", ["tenant_id", "org_node_id", "effective_from", "effective_to", "retention_expiry", "legal_hold"])
def test_source_missing_governance_field_is_denied(rag_store, missing):
    rag_store([{k: v for k, v in SOURCE.items() if k != missing}], [fact("f1", "src_ok")])
    assert scope()["rag_facts"] == []


def test_unknown_source_and_invalid_facts_dropped(rag_store):
    rag_store([SOURCE], [fact("f_ok", "src_ok"), fact("f_orphan", "src_missing"), fact("f_bad", "src_ok", "opinion"),
                         fact("f_nokey", "src_ok", key=""), {**fact("f_nan", "src_ok"), "value": float("nan")},
                         {**fact("f_list_type", "src_ok"), "fact_type": ["threshold"]}])
    assert [f["rag_fact_id"] for f in scope()["rag_facts"]] == ["f_ok"]


def test_duplicate_source_id_fails_closed(rag_store):
    rag_store([SOURCE, {**SOURCE, "legal_hold": True}], [fact("f1", "src_ok")])
    assert scope()["rag_facts"] == []


def test_context_hash_binds_fact_lineage(rag_store):
    rag_store([SOURCE, {**SOURCE, "rag_source_id": "src_two"}], [fact("f1", "src_ok")])
    first = resolve_dual_authority(REQUEST)
    assert first == resolve_dual_authority(REQUEST)
    rag_store([SOURCE, {**SOURCE, "rag_source_id": "src_two"}], [fact("f1", "src_two")])
    assert resolve_dual_authority(REQUEST)["context_hash"] != first["context_hash"]


def test_malformed_rag_store_is_an_error(rag_store, tmp_path):
    (tmp_path / "rag.json").write_text("{not json")
    with pytest.raises(RagStoreError):
        resolve_dual_authority(REQUEST)


def test_eeb_confines_facts_to_eligible_sources_and_lookup(rag_store):
    rag_store([SOURCE, {**SOURCE, "rag_source_id": "src_held", "legal_hold": True}], [fact("f1", "src_ok")])
    auth = resolve_dual_authority(REQUEST)
    eeb = build_evidence_boundary(auth, {"fact_type": "threshold", "key": "supervisor_limit"})
    assert eeb["filters"]["rag_fact"] == {"must": [
        {"terms": {"rag_source_id": ["src_ok"]}},
        {"term": {"fact_type": "threshold"}},
        {"term": {"key": "supervisor_limit"}},
    ]}
    assert eeb["eeb_hash"] == sha256_hex(canonical_json(eeb["filters"]))
    assert eeb["eeb_hash"] != build_evidence_boundary(auth)["eeb_hash"]
    assert build_evidence_boundary(auth, {})["filters"] == build_evidence_boundary(auth)["filters"]


@pytest.mark.parametrize("lookup", [{"fact_type": "opinion"}, {"key": ""}, {"key": 5}, {"visibility": "all"}, ["key"]])
def test_eeb_rejects_invalid_lookup(assignments, lookup):
    with pytest.raises(ValueError):
        build_evidence_boundary(resolve_dual_authority(REQUEST), lookup)


def test_example_rag_stores_load_and_match_example_assignment():
    import pathlib
    from datetime import datetime, timezone

    from ts_sms.dar.rag_store import load_rag_store, resolve_rag_scope

    examples = pathlib.Path(__file__).resolve().parent.parent / "examples"
    scope = {"tenant_id": "tenant_A", "org_node_id": "child_node_tenant_A",
             "role": "analyst", "classification": "CONFIDENTIAL"}
    as_of = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()

    empty = resolve_rag_scope(load_rag_store(str(examples / "rag_store.empty.json")), scope, as_of)
    assert empty == {"eligible_rag_source_ids": [], "rag_facts": []}

    example = resolve_rag_scope(load_rag_store(str(examples / "rag_store.example.json")), scope, as_of)
    assert example["eligible_rag_source_ids"] == ["src_policy_2026"]
    assert [f["rag_fact_id"] for f in example["rag_facts"]] == ["fact_supervisor_limit"]
