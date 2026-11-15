"""Verifies ledger atomicity: contiguous indices, valid hashes, unbroken H_prev linkage."""

from typing import Any, Dict, Iterable, Optional

from ts_sms.crypto.hash_derivation import GENESIS_HASH
from ts_sms.ledger.chain import ALG_HMAC, LedgerIntegrityError, compute_entry_hash

__all__ = ["LedgerIntegrityError", "verify_chain"]


def verify_chain(entries: Iterable[Dict[str, Any]], hmac_key: Optional[str] = None) -> int:
    """
    Return the number of verified entries, or raise LedgerIntegrityError.

    With ``hmac_key`` every entry must be HMAC-keyed, so an attacker without
    the key cannot replace the chain with a recomputed unkeyed one.
    """
    prev_hash = GENESIS_HASH
    count = 0
    seen_v2 = False
    for expected_index, entry in enumerate(entries):
        if entry.get("index") != expected_index:
            raise LedgerIntegrityError(f"Non-contiguous index at position {expected_index}")
        if entry.get("h_prev") != prev_hash:
            raise LedgerIntegrityError(f"Broken H_prev linkage at index {expected_index}")
        if hmac_key and entry.get("alg") != ALG_HMAC:
            raise LedgerIntegrityError(f"Unkeyed entry at index {expected_index} in an HMAC-keyed ledger")
        if entry.get("v") is None and seen_v2:
            raise LedgerIntegrityError(f"Legacy entry after v2 entries at index {expected_index}")
        seen_v2 = seen_v2 or entry.get("v") is not None
        if entry.get("entry_hash") != compute_entry_hash(entry, hmac_key):
            raise LedgerIntegrityError(f"Entry hash mismatch at index {expected_index}")
        prev_hash = entry["entry_hash"]
        count += 1
    if count == 0:
        raise LedgerIntegrityError("Ledger has no Genesis Snapshot (Law 25)")
    return count
