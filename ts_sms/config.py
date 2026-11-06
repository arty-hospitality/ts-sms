"""Runtime configuration, sourced from environment variables."""

import os
from dataclasses import dataclass, field
from typing import Optional

DEFAULT_TEMPORAL_ADDRESS = "localhost:7233"
DEFAULT_TEMPORAL_NAMESPACE = "default"
DEFAULT_TASK_QUEUE = "ts-sms-governance-queue"
DEFAULT_LEDGER_PATH = "ts_sms_ledger.jsonl"
DEFAULT_WORKFLOW_TIMEOUT_SECONDS = 60.0


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_optional(name: str) -> Optional[str]:
    value = os.environ.get(name)
    return value if value else None


@dataclass(frozen=True)
class Settings:
    temporal_address: str = field(
        default_factory=lambda: os.environ.get("TS_SMS_TEMPORAL_ADDRESS", DEFAULT_TEMPORAL_ADDRESS)
    )
    temporal_namespace: str = field(
        default_factory=lambda: os.environ.get("TS_SMS_TEMPORAL_NAMESPACE", DEFAULT_TEMPORAL_NAMESPACE)
    )
    task_queue: str = field(
        default_factory=lambda: os.environ.get("TS_SMS_TASK_QUEUE", DEFAULT_TASK_QUEUE)
    )
    temporal_tls: bool = field(default_factory=lambda: _env_bool("TS_SMS_TEMPORAL_TLS"))
    temporal_tls_client_cert: Optional[str] = field(
        default_factory=lambda: _env_optional("TS_SMS_TEMPORAL_TLS_CLIENT_CERT")
    )
    temporal_tls_client_key: Optional[str] = field(
        default_factory=lambda: _env_optional("TS_SMS_TEMPORAL_TLS_CLIENT_KEY")
    )
    temporal_api_key: Optional[str] = field(
        default_factory=lambda: _env_optional("TS_SMS_TEMPORAL_API_KEY")
    )
    ledger_path: str = field(
        default_factory=lambda: os.environ.get("TS_SMS_LEDGER_PATH", DEFAULT_LEDGER_PATH)
    )
    workflow_timeout_seconds: float = field(
        default_factory=lambda: float(
            os.environ.get("TS_SMS_WORKFLOW_TIMEOUT_SECONDS", DEFAULT_WORKFLOW_TIMEOUT_SECONDS)
        )
    )
    log_level: str = field(default_factory=lambda: os.environ.get("TS_SMS_LOG_LEVEL", "INFO"))


async def connect_client(settings: Settings, target: Optional[str] = None):
    """Connect a Temporal client using the configured address, namespace, TLS and API key."""
    from temporalio.client import Client
    from temporalio.service import TLSConfig

    tls: "bool | TLSConfig" = False
    if settings.temporal_tls_client_cert and settings.temporal_tls_client_key:
        with open(settings.temporal_tls_client_cert, "rb") as f:
            cert = f.read()
        with open(settings.temporal_tls_client_key, "rb") as f:
            key = f.read()
        tls = TLSConfig(client_cert=cert, client_private_key=key)
    elif settings.temporal_tls or settings.temporal_api_key:
        tls = True

    return await Client.connect(
        target or settings.temporal_address,
        namespace=settings.temporal_namespace,
        tls=tls,
        api_key=settings.temporal_api_key,
    )
