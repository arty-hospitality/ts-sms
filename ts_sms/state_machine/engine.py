from datetime import timedelta
from typing import Any, Dict, Optional

from temporalio import workflow
from temporalio.common import RetryPolicy

# Activities wrap the DAR/EEB/Ledger modules
with workflow.unsafe.imports_passed_through():
    from ts_sms.activities import GovernanceActivities
    from ts_sms.eeb.builder import build_evidence_boundary
    from ts_sms.laws.law_29_fail_closed import enforce_fail_closed

# Bounded retries so an activity failure reaches Law 29 instead of retrying forever;
# deterministic validation/integrity errors fail on the first attempt.
ACTIVITY_RETRY_POLICY = RetryPolicy(
    maximum_attempts=3,
    non_retryable_error_types=[
        "ValueError", "KeyError", "TypeError", "AssignmentStoreError", "RagStoreError", "LedgerIntegrityError",
    ],
)

_REQUEST_FIELDS = ("user_id", "tenant_id", "query_hash", "fact_lookup")


class AuthorityDenied(Exception):
    pass


@workflow.defn
class TenantSageStateMachine:
    """
    Formal Implementation of TenantSageStateMachine.tla
    Enforces the deterministic transition from S0 -> S12.
    """

    @workflow.run
    async def run(self, authority_request: dict) -> dict:
        state = "S0"  # INIT
        request: Optional[Dict[str, Any]] = None

        try:
            # TS-CONTRACT-001 fields only; the evaluation time is authoritative workflow time (N-01).
            request = {k: authority_request.get(k) for k in _REQUEST_FIELDS}
            request["evaluated_at"] = workflow.now().timestamp()

            # STATE S1: Authority Resolution (Law 10: Authority Precedes Retrieval)
            state = "S1"
            auth_context = await workflow.execute_activity_method(
                GovernanceActivities.resolve_dual_authority,
                request,
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=ACTIVITY_RETRY_POLICY,
            )

            if auth_context["effect"] == "DENY":
                raise AuthorityDenied(auth_context.get("reason", "Authority Denied by DAR"))

            # STATE S3: EEB Construction
            state = "S3"
            eeb = await workflow.execute_activity(
                build_evidence_boundary,
                args=[auth_context, request.get("fact_lookup")],
                start_to_close_timeout=timedelta(seconds=5),
                retry_policy=ACTIVITY_RETRY_POLICY,
            )

            # STATE S6: Context Governance Layer (CGL) -- not implemented in v1.0 (see README).
            state = "S6"

            # STATE S11 -> S12: Ledger Commit
            state = "S11"
            provenance_envelope = await workflow.execute_activity_method(
                GovernanceActivities.commit_to_ledger,
                {"request": request, "eeb": eeb, "auth_context": auth_context},
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=ACTIVITY_RETRY_POLICY,
            )
            state = "S12"

            return {
                "status": "ALLOW",
                "eeb": eeb,
                "provenance": provenance_envelope
            }

        except Exception as e:
            # LAW 29: FAIL-CLOSED ENFORCEMENT
            cause = getattr(e, "cause", None) or e
            workflow.logger.critical(
                "LAW 29 TRIGGERED. Fail-Closed initiated at state %s: %s: %s",
                state, type(cause).__name__, cause,
            )
            result = enforce_fail_closed(state, e)
            try:
                await workflow.execute_activity_method(
                    GovernanceActivities.record_denial,
                    {"request": request or {}, "failure_state": result["failure_state"]},
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=ACTIVITY_RETRY_POLICY,
                )
            except Exception as ledger_error:
                workflow.logger.error("Law 29 denial could not be ledgered: %s", ledger_error)
            return result
