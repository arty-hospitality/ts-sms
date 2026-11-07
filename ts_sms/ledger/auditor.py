"""Verifies ledger atomicity: contiguous indices, valid hashes, unbroken H_prev linkage."""

from typing import Iterable, Dict, Any

from ts_sms.crypto.hash_derivation import GENESIS_HASH
from ts_sms.ledger.chain import compute_entry_hash


class LedgerIntegrityError(Exception):
    pass


def verify_chain(entries: Iterable[Dict[str, Any]]) -> int:
    """Return the number of verified entries, or raise LedgerIntegrityError."""
    prev_hash = GENESIS_HASH
    count = 0
    for expected_index, entry in enumerate(entries):
        if entry.get("index") != expected_index:
            raise LedgerIntegrityError(f"Non-contiguous index at position {expected_index}")
        if entry.get("h_prev") != prev_hash:
            raise LedgerIntegrityError(f"Broken H_prev linkage at index {expected_index}")
        if entry.get("entry_hash") != compute_entry_hash(entry):
            raise LedgerIntegrityError(f"Entry hash mismatch at index {expected_index}")
        prev_hash = entry["entry_hash"]
        count += 1
    if count == 0:
        raise LedgerIntegrityError("Ledger has no Genesis Snapshot (Law 25)")
    return count
