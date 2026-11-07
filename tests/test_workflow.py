import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio.testing import WorkflowEnvironment

from ts_sms.config import Settings
from ts_sms.interface.gateway import TenantSageGateway
from ts_sms.ledger.auditor import verify_chain
from ts_sms.ledger.chain import get_chain
from ts_sms.worker import bootstrap_ledger, build_worker


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TS_SMS_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("TS_SMS_TASK_QUEUE", f"test-{uuid.uuid4().hex}")
    s = Settings()
    bootstrap_ledger(s)
    return s


async def test_gateway_executes_s0_to_s12(settings):
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with ThreadPoolExecutor() as executor:
            async with build_worker(env.client, settings, executor):
                gateway = TenantSageGateway(settings=settings)
                gateway._client = env.client

                allow = await gateway.execute("user_123", "tenant_A", "Summarize Q3")
                assert allow["status"] == "ALLOW", allow
                assert allow["provenance"]["eeb_hash"] == allow["eeb"]["eeb_hash"]
                assert allow["provenance"]["ledger_index"] == 1

                deny = await gateway.execute("user_123", "", "Summarize Q3")
                assert deny["status"] == "DENY"
                assert deny["failure_state"] == "SF_AUTH"
                assert deny["provenance_hash"] == "NULL_HASH"

    assert verify_chain(get_chain(settings).entries()) == 2


async def test_ledger_failure_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("TS_SMS_LEDGER_PATH", str(tmp_path / "no-genesis.jsonl"))
    monkeypatch.setenv("TS_SMS_TASK_QUEUE", f"test-{uuid.uuid4().hex}")
    settings = Settings()
    async with await WorkflowEnvironment.start_time_skipping() as env:
        with ThreadPoolExecutor() as executor:
            async with build_worker(env.client, settings, executor):
                gateway = TenantSageGateway(settings=settings)
                gateway._client = env.client
                result = await gateway.execute("user_123", "tenant_A", "q")
    assert result["status"] == "DENY"
    assert result["failure_state"] == "SF_LEDGER"
    assert result["eeb"] is None
