import asyncio
from temporalio.client import Client
from typing import Dict, Any, Optional

class TenantSageGateway:
    """
    The STRICT ISOLATION boundary. 
    The RAG application ONLY interacts with this gateway. It has no access to 
    the DAR, the EEB logic, or the ledger.
    """
    def __init__(self, temporal_target: str = "localhost:7233"):
        self.temporal_target = temporal_target

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
        client = await Client.connect(self.temporal_target)
        
        # TS-CONTRACT-001: AuthorityRequest
        authority_request = {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "query": query,
            "timestamp": asyncio.get_event_loop().time(),
            "fact_lookup": fact_lookup
        }

        try:
            # Execute the deterministic S0->S12 State Machine
            result = await client.execute_workflow(
                "TenantSageStateMachine",
                authority_request,
                id=f"ts-sms-exec-{user_id}",
                task_queue="ts-sms-governance-queue",
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