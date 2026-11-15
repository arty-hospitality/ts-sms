import json
from datetime import datetime, timezone

import pytest

from tests.conftest import ASSIGNMENT
from ts_sms.crypto.canonical_serialization import legacy_canonical_json
from ts_sms.crypto.hash_derivation import GENESIS_HASH, sha256_hex
from ts_sms.dar.assignments import AssignmentStoreError
from ts_sms.dar.resolver import resolve_dual_authority
from ts_sms.eeb.builder import build_evidence_boundary
from ts_sms.laws.law_29_fail_closed import enforce_fail_closed
from ts_sms.ledger.auditor import LedgerIntegrityError, verify_chain
from ts_sms.ledger.chain import ProvenanceChain, compute_entry_hash

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp()
REQUEST = {"user_id": "user_123", "tenant_id": "tenant_A", "query_hash": "0" * 64, "evaluated_at": NOW}


# --- DAR (F-01, F-02, N-01, F-09) -------------------------------------------------

def test_dar_allows_active_assignment_with_deterministic_hash(assignments):
    a = resolve_dual_authority(REQUEST)
    assert a["effect"] == "ALLOW"
    assert a["org_scope"] == {"id": "child_node_tenant_A", "level": "Child", "tenant_id": "tenant_A", "role": "analyst"}
    assert a == resolve_dual_authority(REQUEST)


def test_dar_denies_without_assignment_store(monkeypatch):
    monkeypatch.delenv("TS_SMS_ASSIGNMENTS_PATH", raising=False)
    assert resolve_dual_authority(REQUEST)["effect"] == "DENY"


@pytest.mark.parametrize("override", [
    {"tenant_id": "tenant_B"},  # caller names a tenant the user is not assigned to
    {"user_id": "someone_else"},
    {"tenant_id": ""},
    {"evaluated_at": None},
    {"evaluated_at": "2026-06-01"},
])
def test_dar_denies_unauthorized_or_malformed_requests(assignments, override):
    assert resolve_dual_authority({**REQUEST, **override})["effect"] == "DENY"


@pytest.mark.parametrize("record", [
    {**ASSIGNMENT, "effective_to": "2026-03-01T00:00:00Z"},   # expired
    {**ASSIGNMENT, "effective_from": "2026-09-01T00:00:00Z"},  # not yet active
    {k: v for k, v in ASSIGNMENT.items() if k != "effective_to"},  # effective_to undeclared
    {k: v for k, v in ASSIGNMENT.items() if k != "data_classification"},
    {**ASSIGNMENT, "effective_from": "2026-01-01T00:00:00"},  # naive time
])
def test_dar_denies_inactive_or_incomplete_assignments(assignments, record):
    assignments([record])
    assert resolve_dual_authority(REQUEST)["effect"] == "DENY"


def test_dar_denies_ambiguous_assignments(assignments):
    assignments([ASSIGNMENT, {**ASSIGNMENT, "org_node_id": "child_node_other"}])
    assert resolve_dual_authority(REQUEST)["effect"] == "DENY"


def test_data_scope_comes_from_assignment(assignments):
    assignments([{**ASSIGNMENT, "data_classification": "INTERNAL"}])
    a = resolve_dual_authority(REQUEST)
    assert a["data_scope"]["classification"] == "INTERNAL"
    assert {"term": {"classification": "INTERNAL"}} in build_evidence_boundary(a)["filters"]["must"]


def test_context_hash_binds_principal(assignments):
    assignments([ASSIGNMENT, {**ASSIGNMENT, "user_id": "user_456"}])
    a = resolve_dual_authority(REQUEST)
    b = resolve_dual_authority({**REQUEST, "user_id": "user_456"})
    assert a["effect"] == b["effect"] == "ALLOW"
    assert a["context_hash"] != b["context_hash"]


def test_malformed_assignment_store_is_an_error(assignments, tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv("TS_SMS_ASSIGNMENTS_PATH", str(bad))
    with pytest.raises(AssignmentStoreError):
        resolve_dual_authority(REQUEST)


# --- EEB --------------------------------------------------------------------------

def test_eeb_bound_to_context_and_tenant(assignments):
    auth = resolve_dual_authority(REQUEST)
    eeb = build_evidence_boundary(auth)
    assert eeb["auth_context_hash"] == auth["context_hash"]
    assert {"term": {"tenant_id": "tenant_A"}} in eeb["filters"]["must"]
    assert {"term": {"org_node_id": "child_node_tenant_A"}} in eeb["filters"]["must"]
    assert eeb["eeb_hash"] == build_evidence_boundary(auth)["eeb_hash"]


# --- Law 29 (F-04) ----------------------------------------------------------------

@pytest.mark.parametrize("state,expected", [("S1", "SF_AUTH"), ("S3", "SF_EEB"), ("S6", "SF_CGL"), ("S11", "SF_LEDGER"), ("S0", "SF_GOV")])
def test_law_29_maps_states_without_leaking_details(state, expected):
    r = enforce_fail_closed(state, RuntimeError("/secret/path exploded"))
    assert r["failure_state"] == expected
    assert r["eeb"] is None and r["provenance_hash"] == "NULL_HASH"
    assert "details" not in r and "/secret/path" not in json.dumps(r)


# --- Ledger (F-05, F-06) ----------------------------------------------------------

def _append(chain, request_id, decision="ALLOW", **kw):
    return chain.append(request_id, decision, "e" * 64, "c" * 64, "q" * 64, "s" * 64, **kw)


def test_ledger_chain_and_audit(tmp_path):
    chain = ProvenanceChain(str(tmp_path / "ledger.jsonl"))
    genesis = chain.ensure_genesis()
    assert chain.ensure_genesis() == genesis
    e1 = _append(chain, "r1")
    e2 = _append(chain, "r2", "DENY", failure_state="SF_AUTH")
    assert e1["h_prev"] == genesis["entry_hash"]
    assert e2["h_prev"] == e1["entry_hash"]
    assert e2["decision"] == "DENY" and e2["failure_state"] == "SF_AUTH"
    assert _append(chain, "r1", dedupe=True) == e1
    assert verify_chain(chain.entries()) == 3


def test_ledger_audit_detects_tampering(tmp_path):
    path = tmp_path / "ledger.jsonl"
    chain = ProvenanceChain(str(path))
    chain.ensure_genesis()
    _append(chain, "r1")
    lines = path.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["eeb_hash"] = "forged"
    path.write_text(lines[0] + "\n" + json.dumps(entry) + "\n")
    with pytest.raises(LedgerIntegrityError):
        verify_chain(chain.entries())


def _rewrite_unkeyed(path, field, value):
    """Attacker without the key: edit an entry and recompute every hash as unkeyed SHA-256."""
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    entries[1][field] = value
    prev = GENESIS_HASH
    for e in entries:
        e["alg"] = "sha256"
        e["h_prev"] = prev
        e["entry_hash"] = compute_entry_hash(e)
        prev = e["entry_hash"]
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))


def test_unkeyed_ledger_can_be_recomputed_but_hmac_ledger_cannot(tmp_path):
    plain = tmp_path / "plain.jsonl"
    chain = ProvenanceChain(str(plain))
    chain.ensure_genesis()
    _append(chain, "r1")
    _rewrite_unkeyed(plain, "eeb_hash", "f" * 64)
    assert verify_chain(chain.entries()) == 2  # documented residual without a key

    keyed = tmp_path / "keyed.jsonl"
    kchain = ProvenanceChain(str(keyed), hmac_key="k1")
    kchain.ensure_genesis()
    _append(kchain, "r1")
    assert verify_chain(kchain.entries(), "k1") == 2
    with pytest.raises(LedgerIntegrityError):
        verify_chain(kchain.entries(), "wrong-key")
    _rewrite_unkeyed(keyed, "eeb_hash", "f" * 64)
    with pytest.raises(LedgerIntegrityError):
        verify_chain(kchain.entries(), "k1")


def test_hmac_key_refuses_unkeyed_ledger(tmp_path):
    path = str(tmp_path / "ledger.jsonl")
    ProvenanceChain(path).ensure_genesis()
    keyed = ProvenanceChain(path, hmac_key="k1")
    with pytest.raises(LedgerIntegrityError):
        verify_chain(keyed.entries(), "k1")
    with pytest.raises(LedgerIntegrityError):
        _append(keyed, "r1")


def test_hmac_ledger_requires_key_to_verify(tmp_path):
    chain = ProvenanceChain(str(tmp_path / "ledger.jsonl"), hmac_key="k1")
    chain.ensure_genesis()
    with pytest.raises(LedgerIntegrityError):
        verify_chain(chain.entries())


def test_v1_ledger_remains_verifiable_and_extendable(tmp_path):
    path = tmp_path / "ledger.jsonl"
    fields = ("index", "request_id", "timestamp", "h_prev", "eeb_hash", "context_hash", "query_hash")
    v1 = {"index": 0, "request_id": "GENESIS", "timestamp": 1.0, "h_prev": GENESIS_HASH,
          "eeb_hash": GENESIS_HASH, "context_hash": GENESIS_HASH, "query_hash": GENESIS_HASH}
    v1["entry_hash"] = sha256_hex(legacy_canonical_json({k: v1[k] for k in fields}))
    path.write_text(json.dumps(v1, sort_keys=True) + "\n")
    chain = ProvenanceChain(str(path))
    _append(chain, "r1")
    assert verify_chain(chain.entries()) == 2


def test_torn_tail_is_an_integrity_error(tmp_path):
    path = tmp_path / "ledger.jsonl"
    chain = ProvenanceChain(str(path))
    chain.ensure_genesis()
    with open(path, "a") as f:
        f.write('{"index": 1, "trunc')
    with pytest.raises(LedgerIntegrityError):
        _append(chain, "r1")


def test_append_without_genesis_fails(tmp_path):
    with pytest.raises(LedgerIntegrityError):
        _append(ProvenanceChain(str(tmp_path / "l.jsonl")), "r")
