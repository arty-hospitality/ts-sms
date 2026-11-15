import logging
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio.client import Client

from ts_sms.config import Settings, connect_client
from ts_sms.crypto.hash_derivation import keyed_digest

logger = logging.getLogger("ts_sms.gateway")


class TenantSageGateway:
    """
    The STRICT ISOLATION boundary.
    The RAG application ONLY interacts with this gateway. It has no access to
    the DAR, the EEB logic, or the ledger.

    Connection settings default to the ``TS_SMS_*`` environment variables
    (see ``ts_sms.config.Settings``); ``temporal_target`` overrides the address.
    """
    def __init__(self, temporal_target: Optional[str] = None, settings: Optional[Settings] = None):
        self.settings = settings or Settings()
        self.temporal_target = temporal_target or self.settings.temporal_address
        self._client: Optional[Client] = None

    async def _get_client(self) -> Client:
        if self._client is None:
            self._client = await connect_client(self.settings, target=self.temporal_target)
        return self._client

    async def execute(
        self,
        user_id: str,
        tenant_id: str,
        query: str,
        fact_lookup: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Submits the execution request to the TS-SMS Temporal State Machine.
        The application is locked out until the state machine reaches S12 (Ledger Commit)
        or hits a Failure State (SF_AUTH, SF_GOV, etc.).

        fact_lookup: optional precise rag_fact lookup, e.g. {"fact_type": "threshold", "key": "supervisor_limit"}.
        It narrows rag_fact retrieval within the EEB and supplements, never replaces, chunk retrieval.
        """
        # TS-CONTRACT-001: AuthorityRequest. The raw query never enters Temporal;
        # the evaluation time is set by the workflow, not the caller.
        authority_request = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "query_hash": keyed_digest(str(query), self.settings.digest_key),
        }
        if fact_lookup is not None:
            authority_request["fact_lookup"] = fact_lookup

        try:
            client = await self._get_client()

            # Execute the deterministic S0->S12 State Machine
            result = await client.execute_workflow(
                "TenantSageStateMachine",
                authority_request,
                id=f"ts-sms-exec-{uuid.uuid4().hex}",
                task_queue=self.settings.task_queue,
                execution_timeout=timedelta(seconds=self.settings.workflow_timeout_seconds),
            )
            return result

        except Exception:
            logger.warning("TS-SMS governance execution failed", exc_info=True)
            # TS-CONTRACT-011: FailureResponse mapping
            return {
                "status": "DENY",
                "failure_state": "SF_GOV",
                "reason": "Governance Execution Failure",
                "eeb": None,
                "provenance_hash": "NULL_HASH"
            }
