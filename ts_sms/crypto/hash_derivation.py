import hashlib

NULL_HASH = "NULL_HASH"
GENESIS_HASH = "0" * 64


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
