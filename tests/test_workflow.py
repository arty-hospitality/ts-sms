import base64
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.api.enums.v1 import RetryState
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from tests.conftest import ASSIGNMENT
from ts_sms.config import Settings, data_converter
from ts_sms.interface.gateway import TenantSageGateway
from ts_sms.ledger.auditor import verify_chain
from ts_sms.ledger.chain import get_chain
from ts_sms.worker import bootstrap_ledger, build_worker


class RecordingClient:
    """Delegates to a Temporal client and records the workflow ids the gateway starts."""

    def __init__(self, client):
        self._client = client
        self.calls = []

    async def execute_workflow(self, workflow, arg, **kwargs):
        self.calls.append((arg, kwargs))
        return await self._client.execute_workflow(workflow, arg, **kwargs)


@pytest.fixture
def settings(tmp_path, monkeypatch, assignments):
    monkeypatch.setenv("TS_SMS_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("TS_SMS_TASK_QUEUE", f"test-{uuid.uuid4().hex}")
    s = Settings()
    bootstrap_ledger(s)
    return s


def _gateway(settings, client):
    gateway = TenantSageGateway(settings=settings)
    gateway._client = RecordingClient(client)
    return gateway


async def test_gateway_executes_s0_to_s12_and_ledgers_denials(settings):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with ThreadPoolExecutor() as executor:
            async with build_worker(env.client, settings, executor):
                gateway = _gateway(settings, env.client)

                allow = await gateway.execute("user_123", "tenant_A", "Summarize Q3")
                assert allow["status"] == "ALLOW", allow
                assert allow["provenance"]["eeb_hash"] == allow["eeb"]["eeb_hash"]
                assert allow["provenance"]["ledger_index"] == 1

                # F-01: caller-selected tenant without an assignment is denied
                deny = await gateway.execute("user_123", "tenant_B", "Summarize Q3")
                assert deny["status"] == "DENY"
                assert deny["failure_state"] == "SF_AUTH"
                assert deny["provenance_hash"] == "NULL_HASH"
                assert "details" not in deny

                # F-04: no raw query, caller timestamp or user_id leaves the gateway
                for arg, kwargs in gateway._client.calls:
                    assert set(arg) == {"user_id", "tenant_id", "query_hash"}
                    assert "user_123" not in kwargs["id"]
                    history = await env.client.get_workflow_handle(kwargs["id"]).fetch_history()
                    assert "Summarize Q3" not in history.to_json()

    entries = list(get_chain(settings).entries())
    assert verify_chain(entries) == 3
    assert [e["decision"] for e in entries] == ["GENESIS", "ALLOW", "DENY"]
    assert entries[2]["failure_state"] == "SF_AUTH"
    assert entries[1]["subject_hash"] != entries[2]["subject_hash"]


async def test_expired_assignment_is_denied_at_workflow_time(settings, assignments):
    assignments([{**ASSIGNMENT, "effective_to": "2026-01-02T00:00:00Z"}])
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with ThreadPoolExecutor() as executor:
            async with build_worker(env.client, settings, executor):
                result = await _gateway(settings, env.client).execute("user_123", "tenant_A", "q")
    assert result["status"] == "DENY" and result["failure_state"] == "SF_AUTH"


async def test_malformed_assignment_store_fails_closed_without_retry(settings, tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setenv("TS_SMS_ASSIGNMENTS_PATH", str(bad))
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with ThreadPoolExecutor() as executor:
            async with build_worker(env.client, settings, executor):
                gateway = _gateway(settings, env.client)
                result = await gateway.execute("user_123", "tenant_A", "q")
                wf_id = gateway._client.calls[0][1]["id"]
                history = await env.client.get_workflow_handle(wf_id).fetch_history()
    assert result["status"] == "DENY" and result["failure_state"] == "SF_AUTH"
    started = [e.activity_task_started_event_attributes for e in history.events
               if e.HasField("activity_task_started_event_attributes")]
    failed = [e.activity_task_failed_event_attributes for e in history.events
              if e.HasField("activity_task_failed_event_attributes")]
    assert [a.attempt for a in started] == [1, 1]  # resolve_dual_authority once, then record_denial
    assert failed[0].retry_state == RetryState.RETRY_STATE_NON_RETRYABLE_FAILURE


async def test_ledger_failure_fails_closed(tmp_path, monkeypatch, assignments):
    monkeypatch.setenv("TS_SMS_LEDGER_PATH", str(tmp_path / "no-genesis.jsonl"))
    monkeypatch.setenv("TS_SMS_TASK_QUEUE", f"test-{uuid.uuid4().hex}")
    settings = Settings()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with ThreadPoolExecutor() as executor:
            async with build_worker(env.client, settings, executor):
                result = await _gateway(settings, env.client).execute("user_123", "tenant_A", "q")
    assert result["status"] == "DENY"
    assert result["failure_state"] == "SF_LEDGER"
    assert result["eeb"] is None


async def test_payload_encryption_hides_identifiers_from_history(settings, monkeypatch):
    monkeypatch.setenv("TS_SMS_PAYLOAD_KEY", base64.b64encode(os.urandom(32)).decode())
    encrypted_settings = Settings()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        config = env.client.config()
        config["data_converter"] = data_converter(encrypted_settings)
        encrypted_client = Client(**config)
        with ThreadPoolExecutor() as executor:
            async with build_worker(encrypted_client, encrypted_settings, executor):
                gateway = _gateway(encrypted_settings, encrypted_client)
                result = await gateway.execute("user_123", "tenant_A", "q")
                assert result["status"] == "ALLOW", result
                wf_id = gateway._client.calls[0][1]["id"]
                raw = await env.client.get_workflow_handle(wf_id).fetch_history()
    assert "dXNlcl8xMjM" not in raw.to_json()  # base64("user_123")
    assert "user_123" not in raw.to_json()
