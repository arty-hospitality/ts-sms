"""AES-256-GCM PayloadCodec so workflow inputs/results are encrypted in Temporal history."""

import base64
import os
from typing import List, Sequence

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

ENCODING = b"binary/encrypted-aes256gcm"
_NONCE_BYTES = 12


class EncryptionCodec(PayloadCodec):
    def __init__(self, key: bytes, key_id: str = "default"):
        if len(key) != 32:
            raise ValueError("TS_SMS_PAYLOAD_KEY must decode to 32 bytes (AES-256)")
        self._aead = AESGCM(key)
        self._key_id = key_id

    @classmethod
    def from_b64(cls, value: str) -> "EncryptionCodec":
        return cls(base64.b64decode(value, validate=True))

    async def encode(self, payloads: Sequence[Payload]) -> List[Payload]:
        out = []
        for p in payloads:
            nonce = os.urandom(_NONCE_BYTES)
            ciphertext = self._aead.encrypt(nonce, p.SerializeToString(), self._key_id.encode())
            out.append(Payload(
                metadata={"encoding": ENCODING, "encryption-key-id": self._key_id.encode()},
                data=nonce + ciphertext,
            ))
        return out

    async def decode(self, payloads: Sequence[Payload]) -> List[Payload]:
        out = []
        for p in payloads:
            if p.metadata.get("encoding", b"") != ENCODING:
                out.append(p)
                continue
            key_id = p.metadata.get("encryption-key-id", b"").decode()
            if key_id != self._key_id:
                raise ValueError(f"Unknown payload encryption key id {key_id!r}")
            plaintext = self._aead.decrypt(p.data[:_NONCE_BYTES], p.data[_NONCE_BYTES:], key_id.encode())
            out.append(Payload.FromString(plaintext))
        return out
