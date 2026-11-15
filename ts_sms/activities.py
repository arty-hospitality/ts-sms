"""Temporal activities bound to the worker's effective Settings (DAR S1, ledger S11/S12, Law 29 denials)."""

from typing import Any, Dict

from temporalio import activity

from ts_sms.config import Settings
from ts_sms.dar import resolver
from ts_sms.ledger import chain


class GovernanceActivities:
    """One instance per worker so bootstrap and every activity share the same configuration."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @activity.defn(name="resolve_dual_authority")
    def resolve_dual_authority(self, request: Dict[str, Any]) -> Dict[str, Any]:
        return resolver.resolve_dual_authority(request, self.settings)

    @activity.defn(name="commit_to_ledger")
    def commit_to_ledger(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return chain.commit_to_ledger(payload, self.settings)

    @activity.defn(name="record_denial")
    def record_denial(self, payload: Dict[str, Any]) -> int:
        return chain.record_denial(payload, self.settings)
