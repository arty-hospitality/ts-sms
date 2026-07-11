from temporalio import workflow
from temporalio.exceptions import ApplicationError
from datetime import timedelta
import logging

# Import activities (omitted for brevity, but they wrap the DAR/EEB/Ledger modules)
with workflow.unsafe.imports_passed_through():
    from ts_sms.dar.resolver import resolve_dual_authority
    from ts_sms.eeb.builder import build_evidence_boundary
    from ts_sms.ledger.chain import commit_to_ledger
    from ts_sms.laws.law_29_fail_closed import enforce_fail_closed

@workflow.defn
class TenantSageStateMachine:
    """
    Formal Implementation of TenantSageStateMachine.tla
    Enforces the deterministic transition from S0 -> S12.
    """
    
    @workflow.run
    async def run(self, authority_request: dict) -> dict:
        state = "S0" # INIT
        logger = logging.getLogger("ts_sms_engine")
        
        try:
            # STATE S1: Authority Resolution (Law 10: Authority Precedes Retrieval)
            state = "S1"
            auth_context = await workflow.execute_activity(
                resolve_dual_authority,
                authority_request,
                start_to_close_timeout=timedelta(seconds=5),
            )
            
            if auth_context["effect"] == "DENY":
                raise ApplicationError("Authority Denied by DAR", type="SF_AUTH")

            # STATE S3: EEB Construction
            state = "S3"
            eeb = await workflow.execute_activity(
                build_evidence_boundary,
                auth_context,
                start_to_close_timeout=timedelta(seconds=5),
            )

            # STATE S6: Context Governance Layer (CGL) / Prompt Inspection
            state = "S6"
            # (CGL logic omitted for brevity, assumes pass)

            # STATE S11 -> S12: Ledger Commit
            state = "S11"
            provenance_envelope = await workflow.execute_activity(
                commit_to_ledger,
                {"request": authority_request, "eeb": eeb, "auth_context": auth_context},
                start_to_close_timeout=timedelta(seconds=10),
            )
            state = "S12"

            return {
                "status": "ALLOW",
                "eeb": eeb,
                "provenance": provenance_envelope
            }

        except Exception as e:
            # LAW 29: FAIL-CLOSED ENFORCEMENT
            # Destroys context and routes to formal failure states (SF_AUTH, SF_EEB, etc.)
            return enforce_fail_closed(state, e)