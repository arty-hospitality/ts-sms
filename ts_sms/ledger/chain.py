"""
Append-only provenance ledger (States S11 -> S12).

Each entry is one JSON line linked to its predecessor via ``h_prev``. The
first line is the Genesis Snapshot (Law 25), written at bootstrap. Appends
take an exclusive ``flock`` so several worker processes on the same host /
volume can share one ledger file safely.
"""

import fcntl
import json
import os
import time
from typing import Any, Dict, Iterator, Optional

from temporalio import activity

from ts_sms.config import Settings
from ts_sms.crypto.canonical_serialization import canonical_json
from ts_sms.crypto.hash_derivation import GENESIS_HASH, sha256_hex

_HASHED_FIELDS = (
    "index",
    "request_id",
    "timestamp",
    "h_prev",
    "eeb_hash",
    "context_hash",
    "query_hash",
)


def compute_entry_hash(entry: Dict[str, Any]) -> str:
    return sha256_hex(canonical_json({k: entry.get(k) for k in _HASHED_FIELDS}))


def _make_entry(index: int, request_id: str, h_prev: str, eeb_hash: str,
                context_hash: str, query_hash: str) -> Dict[str, Any]:
    entry = {
        "index": index,
        "request_id": request_id,
        "timestamp": time.time(),
        "h_prev": h_prev,
        "eeb_hash": eeb_hash,
        "context_hash": context_hash,
        "query_hash": query_hash,
    }
    entry["entry_hash"] = compute_entry_hash(entry)
    return entry


class ProvenanceChain:
    """File-backed append-only ledger with H_prev parent-hash linkage."""

    def __init__(self, path: str):
        self.path = path

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
                genesis = _make_entry(0, "GENESIS", GENESIS_HASH, GENESIS_HASH,
                                      GENESIS_HASH, GENESIS_HASH)
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

    def find(self, request_id: str) -> Optional[Dict[str, Any]]:
        for entry in self.entries():
            if entry["request_id"] == request_id:
                return entry
        return None

    def append(self, request_id: str, eeb_hash: str, context_hash: str,
               query_hash: str, dedupe: bool = False) -> Dict[str, Any]:
        with self._open() as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                if dedupe:
                    existing = self.find(request_id)
                    if existing is not None:
                        return existing
                last_line = self._last_line(f)
                if last_line is None:
                    raise RuntimeError("Ledger has no Genesis Snapshot (Law 25)")
                prev = json.loads(last_line)
                entry = _make_entry(prev["index"] + 1, request_id, prev["entry_hash"],
                                    eeb_hash, context_hash, query_hash)
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
    return ProvenanceChain((settings or Settings()).ledger_path)


@activity.defn
def commit_to_ledger(payload: Dict[str, Any]) -> Dict[str, Any]:
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

    info = activity.info()
    request_id = f"{info.workflow_id}/{info.workflow_run_id}"
    query_hash = sha256_hex(str(request.get("query", "")))

    entry = get_chain().append(
        request_id=request_id,
        eeb_hash=eeb["eeb_hash"],
        context_hash=auth_context["context_hash"],
        query_hash=query_hash,
        dedupe=info.attempt > 1,
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
