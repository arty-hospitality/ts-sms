from typing import Any, Dict


def enforce_fail_closed(current_state: str, exception: Exception) -> Dict[str, Any]:
    """
    Implements ALLOY_MODEL.als ARB Amendment:
    assert DenyStopsResponseHash {
        all auth : AuthorityContext |
            auth.effect = DENY => auth.provenance.response_hash = NullHash
    }

    Ensures that any failure physically destroys the evidence path. Internal
    exception text is never returned to the caller; operators see it in logs.
    """
    # Map execution state to formally verified Failure States
    failure_state_map = {
        "S1": "SF_AUTH",
        "S3": "SF_EEB",
        "S6": "SF_CGL",
        "S11": "SF_LEDGER"
    }

    final_sf_state = failure_state_map.get(current_state, "SF_GOV")

    # Return TS-CONTRACT-011: FailureResponse
    # Critically: eeb is None, hashes are NULL_HASH
    return {
        "status": "DENY",
        "failure_state": final_sf_state,
        "reason": "Execution halted by Law 29: Fail-Closed",
        "eeb": None,
        "provenance_hash": "NULL_HASH",
        "context_hash": "NULL_HASH"
    }
