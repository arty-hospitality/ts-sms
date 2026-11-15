import hashlib
import hmac
from typing import Optional

NULL_HASH = "NULL_HASH"
GENESIS_HASH = "0" * 64


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def keyed_digest(data: str, key: Optional[str]) -> str:
    """HMAC-SHA256 when a key is configured, plain SHA-256 otherwise."""
    if not key:
        return sha256_hex(data)
    return hmac.new(key.encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()
