import time
import uuid
from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio.client import Client

from ts_sms.config import Settings, connect_client


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

    async def execute(self, user_id: str, tenant_id: str, query: str) -> Dict[str, Any]:
        """
        Submits the execution request to the TS-SMS Temporal State Machine.
        The application is locked out until the state machine reaches S12 (Ledger Commit)
        or hits a Failure State (SF_AUTH, SF_GOV, etc.).
        """
        # TS-CONTRACT-001: AuthorityRequest
        authority_request = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "query": query,
            "timestamp": time.time(),
        }

        try:
            client = await self._get_client()

            # Execute the deterministic S0->S12 State Machine
            result = await client.execute_workflow(
                "TenantSageStateMachine",
                authority_request,
                id=f"ts-sms-exec-{user_id}-{uuid.uuid4().hex}",
                task_queue=self.settings.task_queue,
                execution_timeout=timedelta(seconds=self.settings.workflow_timeout_seconds),
            )
            return result

        except Exception as e:
            # TS-CONTRACT-011: FailureResponse mapping
            return {
                "status": "DENY",
                "failure_state": "SF_GOV",
                "reason": "Governance Execution Failure",
                "details": str(e),
                "eeb": None,
                "provenance_hash": "NULL_HASH"
            }
