"""
Append-only provenance ledger (States S11 -> S12, and every Law 29 denial).

Each entry is one JSON line linked to its predecessor via ``h_prev``. The
first line is the Genesis Snapshot (Law 25), written at bootstrap. Appends
take an exclusive ``flock`` so several worker processes on the same host /
volume can share one ledger file safely.

v2 entries record the decision (ALLOW/DENY), the failure state and a digest
of the principal + tenant. With ``TS_SMS_LEDGER_HMAC_KEY`` set, every entry
hash is an HMAC-SHA256, so the chain cannot be rewritten consistently
without the key; a keyed deployment refuses to extend an unkeyed ledger.
v1 entries (no ``v`` field) written before this format remain verifiable.
"""

import fcntl
import json
import os
import re
import time
from typing import Any, Dict, Iterator, Optional

from temporalio import activity

from ts_sms.config import Settings
from ts_sms.crypto.canonical_serialization import canonical_json, legacy_canonical_json
from ts_sms.crypto.hash_derivation import GENESIS_HASH, NULL_HASH, keyed_digest, sha256_hex

ENTRY_VERSION = 2
ALG_SHA256 = "sha256"
ALG_HMAC = "hmac-sha256"

_V1_HASHED_FIELDS = (
    "index", "request_id", "timestamp", "h_prev", "eeb_hash", "context_hash", "query_hash",
)
_V2_HASHED_FIELDS = (
    "v", "alg", "index", "request_id", "timestamp", "h_prev", "decision",
    "failure_state", "eeb_hash", "context_hash", "query_hash", "subject_hash",
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class LedgerIntegrityError(ValueError):
    pass


def compute_entry_hash(entry: Dict[str, Any], hmac_key: Optional[str] = None) -> str:
    if "v" not in entry:
        if "alg" in entry:
            raise LedgerIntegrityError("Legacy (v1) entry must not carry an alg field")
        return sha256_hex(legacy_canonical_json({k: entry.get(k) for k in _V1_HASHED_FIELDS}))
    if entry["v"] != ENTRY_VERSION:
        raise LedgerIntegrityError(f"Unknown ledger entry version {entry['v']!r}")
    payload = canonical_json({k: entry.get(k) for k in _V2_HASHED_FIELDS})
    if entry.get("alg") == ALG_HMAC:
        if not hmac_key:
            raise LedgerIntegrityError("Ledger is HMAC-keyed; TS_SMS_LEDGER_HMAC_KEY is required")
        return keyed_digest(payload, hmac_key)
    if entry.get("alg") == ALG_SHA256:
        return sha256_hex(payload)
    raise LedgerIntegrityError(f"Unknown ledger hash algorithm {entry.get('alg')!r}")


def subject_digest(user_id: Any, tenant_id: Any, key: Optional[str]) -> str:
    return keyed_digest(canonical_json({"user_id": user_id, "tenant_id": tenant_id}), key)


class ProvenanceChain:
    """File-backed append-only ledger with H_prev parent-hash linkage."""

    def __init__(self, path: str, hmac_key: Optional[str] = None):
        self.path = path
        self.hmac_key = hmac_key
        self.alg = ALG_HMAC if hmac_key else ALG_SHA256

    def _make_entry(self, index: int, request_id: str, h_prev: str, decision: str,
                    failure_state: Optional[str], eeb_hash: str, context_hash: str,
                    query_hash: str, subject_hash: str) -> Dict[str, Any]:
        entry = {
            "v": ENTRY_VERSION,
            "alg": self.alg,
            "index": index,
            "request_id": request_id,
            "timestamp": time.time(),
            "h_prev": h_prev,
            "decision": decision,
            "failure_state": failure_state,
            "eeb_hash": eeb_hash,
            "context_hash": context_hash,
            "query_hash": query_hash,
            "subject_hash": subject_hash,
        }
        entry["entry_hash"] = compute_entry_hash(entry, self.hmac_key)
        return entry

    def _open(self):
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        return open(self.path, "a+b")

    def ensure_genesis(self) -> Dict[str, Any]:
        """Law 25: the Genesis Snapshot must exist at system bootstrap."""
        with self._open() as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0)
                first = f.readline()
                if first:
                    return json.loads(first)
                genesis = self._make_entry(0, "GENESIS", GENESIS_HASH, "GENESIS", None,
                                           GENESIS_HASH, GENESIS_HASH, GENESIS_HASH, GENESIS_HASH)
                self._write(f, genesis)
                return genesis
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def entries(self) -> Iterator[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return
        with open(self.path, "rb") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

    def find(self, request_id: str, decision: str) -> Optional[Dict[str, Any]]:
        for entry in self.entries():
            if entry["request_id"] == request_id and entry.get("decision", "ALLOW") == decision:
                return entry
        return None

    def append(self, request_id: str, decision: str, eeb_hash: str, context_hash: str,
               query_hash: str, subject_hash: str, failure_state: Optional[str] = None,
               dedupe: bool = False) -> Dict[str, Any]:
        with self._open() as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                if dedupe:
                    existing = self.find(request_id, decision)
                    if existing is not None:
                        return existing
                last_line = self._last_line(f)
                if last_line is None:
                    raise LedgerIntegrityError("Ledger has no Genesis Snapshot (Law 25)")
                try:
                    prev = json.loads(last_line)
                except json.JSONDecodeError:
                    raise LedgerIntegrityError("Ledger tail is torn or corrupt; refusing to append") from None
                if self.hmac_key and prev.get("alg") != ALG_HMAC:
                    raise LedgerIntegrityError("HMAC key configured but ledger tail is not HMAC-keyed")
                entry = self._make_entry(prev["index"] + 1, request_id, prev["entry_hash"], decision,
                                         failure_state, eeb_hash, context_hash, query_hash, subject_hash)
                self._write(f, entry)
                return entry
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    @staticmethod
    def _last_line(f) -> Optional[str]:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return None
        pos = size
        buf = b""
        while pos > 0:
            step = min(4096, pos)
            pos -= step
            f.seek(pos)
            buf = f.read(step) + buf
            stripped = buf.rstrip(b"\n")
            if b"\n" in stripped:
                return stripped.rsplit(b"\n", 1)[1].decode("utf-8")
        stripped = buf.rstrip(b"\n")
        return stripped.decode("utf-8") if stripped else None

    @staticmethod
    def _write(f, entry: Dict[str, Any]) -> None:
        f.seek(0, os.SEEK_END)
        f.write((canonical_json(entry) + "\n").encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def get_chain(settings: Optional[Settings] = None) -> ProvenanceChain:
    settings = settings or Settings()
    return ProvenanceChain(settings.ledger_path, settings.ledger_hmac_key)


def _request_id() -> str:
    info = activity.info()
    return f"{info.workflow_id}/{info.workflow_run_id}"


def _query_hash(request: Dict[str, Any]) -> Optional[str]:
    value = request.get("query_hash")
    return value if isinstance(value, str) and _HEX64.match(value) else None


def commit_to_ledger(payload: Dict[str, Any], settings: Optional[Settings] = None) -> Dict[str, Any]:
    """
    S11 -> S12: bind H_prev to the EEB hash and governance context hash,
    producing the TS-CONTRACT-005 ProvenanceEnvelope.

    Idempotent per workflow run: a retried attempt returns the entry already
    written by an earlier attempt instead of appending a duplicate.
    """
    eeb = payload["eeb"]
    auth_context = payload["auth_context"]
    request = payload["request"]

    if eeb["auth_context_hash"] != auth_context["context_hash"]:
        raise ValueError("EEB is not bound to the resolved governance context")
    query_hash = _query_hash(request)
    if query_hash is None:
        raise ValueError("AuthorityRequest is missing a valid query_hash")

    settings = settings or Settings()
    entry = get_chain(settings).append(
        request_id=_request_id(),
        decision="ALLOW",
        eeb_hash=eeb["eeb_hash"],
        context_hash=auth_context["context_hash"],
        query_hash=query_hash,
        subject_hash=subject_digest(request.get("user_id"), request.get("tenant_id"), settings.digest_key),
        dedupe=activity.info().attempt > 1,
    )

    return {
        "ledger_index": entry["index"],
        "h_prev": entry["h_prev"],
        "provenance_hash": entry["entry_hash"],
        "eeb_hash": entry["eeb_hash"],
        "context_hash": entry["context_hash"],
        "query_hash": entry["query_hash"],
        "committed_at": entry["timestamp"],
    }


def record_denial(payload: Dict[str, Any], settings: Optional[Settings] = None) -> int:
    """
    Law 29 audit trail: append a DENY entry (EEB and context NULL_HASH) for a
    failed run. If an ALLOW entry was written for the same run before the
    workflow failed, this DENY entry supersedes it.
    """
    request = payload.get("request") or {}
    settings = settings or Settings()
    entry = get_chain(settings).append(
        request_id=_request_id(),
        decision="DENY",
        failure_state=str(payload.get("failure_state") or "SF_GOV"),
        eeb_hash=NULL_HASH,
        context_hash=NULL_HASH,
        query_hash=_query_hash(request) or NULL_HASH,
        subject_hash=subject_digest(request.get("user_id"), request.get("tenant_id"), settings.digest_key),
        dedupe=activity.info().attempt > 1,
    )
    return entry["index"]
