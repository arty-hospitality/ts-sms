"""``ts-sms-worker``: Temporal worker hosting the TS-SMS governance state machine."""

import asyncio
import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from temporalio.client import Client
from temporalio.worker import Worker

from ts_sms.config import Settings, connect_client
from ts_sms.dar.resolver import resolve_dual_authority
from ts_sms.eeb.builder import build_evidence_boundary
from ts_sms.ledger.auditor import verify_chain
from ts_sms.ledger.chain import commit_to_ledger, get_chain
from ts_sms.state_machine.engine import TenantSageStateMachine

logger = logging.getLogger("ts_sms.worker")

ACTIVITIES = [resolve_dual_authority, build_evidence_boundary, commit_to_ledger]
WORKFLOWS = [TenantSageStateMachine]


def bootstrap_ledger(settings: Settings) -> None:
    """Law 25: write the Genesis Snapshot if absent, then refuse to start on a corrupted ledger."""
    chain = get_chain(settings)
    chain.ensure_genesis()
    count = verify_chain(chain.entries())
    logger.info("Ledger %s verified (%d entries)", settings.ledger_path, count)


async def connect_with_retry(settings: Settings, attempts: int = 30, delay: float = 2.0) -> Client:
    for attempt in range(1, attempts + 1):
        try:
            return await connect_client(settings)
        except Exception as e:
            if attempt == attempts:
                raise
            logger.warning("Temporal at %s not reachable (%s); retry %d/%d in %.0fs",
                           settings.temporal_address, e, attempt, attempts, delay)
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable")


def build_worker(client: Client, settings: Settings, executor: ThreadPoolExecutor) -> Worker:
    return Worker(
        client,
        task_queue=settings.task_queue,
        workflows=WORKFLOWS,
        activities=ACTIVITIES,
        activity_executor=executor,
    )


async def run_worker(settings: Optional[Settings] = None) -> None:
    settings = settings or Settings()
    bootstrap_ledger(settings)
    client = await connect_with_retry(settings)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    with ThreadPoolExecutor(max_workers=32) as executor:
        worker = build_worker(client, settings, executor)
        logger.info("TS-SMS worker polling task queue %r on %s (namespace %r)",
                    settings.task_queue, settings.temporal_address, settings.temporal_namespace)
        async with worker:
            await stop.wait()
            logger.info("Shutdown signal received; draining worker")


def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
