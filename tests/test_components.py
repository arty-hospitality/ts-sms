import json

import pytest

from ts_sms.dar.resolver import resolve_dual_authority
from ts_sms.eeb.builder import build_evidence_boundary
from ts_sms.laws.law_29_fail_closed import enforce_fail_closed
from ts_sms.ledger.auditor import LedgerIntegrityError, verify_chain
from ts_sms.ledger.chain import ProvenanceChain

REQUEST = {"user_id": "u1", "tenant_id": "tA", "query": "q", "timestamp": 1.0}


def test_dar_allow_and_deterministic_hash():
    a = resolve_dual_authority(REQUEST)
    assert a["effect"] == "ALLOW"
    assert a["org_scope"]["id"] == "child_node_tA"
    assert a == resolve_dual_authority(REQUEST)


def test_dar_denies_missing_tenant():
    assert resolve_dual_authority({**REQUEST, "tenant_id": ""})["effect"] == "DENY"


def test_eeb_bound_to_context():
    auth = resolve_dual_authority(REQUEST)
    eeb = build_evidence_boundary(auth)
    assert eeb["auth_context_hash"] == auth["context_hash"]
    assert {"term": {"org_node_id": "child_node_tA"}} in eeb["filters"]["must"]
    assert eeb["eeb_hash"] == build_evidence_boundary(auth)["eeb_hash"]


@pytest.mark.parametrize("state,expected", [("S1", "SF_AUTH"), ("S3", "SF_EEB"), ("S11", "SF_LEDGER"), ("S0", "SF_GOV")])
def test_law_29_maps_states(state, expected):
    r = enforce_fail_closed(state, RuntimeError("x"))
    assert r["failure_state"] == expected
    assert r["eeb"] is None and r["provenance_hash"] == "NULL_HASH"


def test_ledger_chain_and_audit(tmp_path):
    chain = ProvenanceChain(str(tmp_path / "ledger.jsonl"))
    genesis = chain.ensure_genesis()
    assert chain.ensure_genesis() == genesis
    e1 = chain.append("r1", "e", "c", "q")
    e2 = chain.append("r2", "e", "c", "q")
    assert e1["h_prev"] == genesis["entry_hash"]
    assert e2["h_prev"] == e1["entry_hash"]
    assert chain.append("r1", "e", "c", "q", dedupe=True) == e1
    assert verify_chain(chain.entries()) == 3


def test_ledger_audit_detects_tampering(tmp_path):
    path = tmp_path / "ledger.jsonl"
    chain = ProvenanceChain(str(path))
    chain.ensure_genesis()
    chain.append("r1", "e", "c", "q")
    lines = path.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["eeb_hash"] = "forged"
    path.write_text(lines[0] + "\n" + json.dumps(entry) + "\n")
    with pytest.raises(LedgerIntegrityError):
        verify_chain(chain.entries())


def test_append_without_genesis_fails(tmp_path):
    with pytest.raises(RuntimeError):
        ProvenanceChain(str(tmp_path / "l.jsonl")).append("r", "e", "c", "q")
