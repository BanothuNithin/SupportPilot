import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from app.core.config import settings
from app.jobs.handlers import handle_index_document
from app.jobs.queue import dequeue
from app.jobs.reconciler import reconcile_once

log = logging.getLogger(__name__)
JobHandler = Callable[[str, str], Awaitable[None]]
handlers: dict[str, JobHandler] = {"index_document": handle_index_document}


async def _reconcile_loop() -> None:
    while True:
        try:
            await reconcile_once()
        except Exception:
            log.warning("job reconciliation failed")
        await asyncio.sleep(settings.reconcile_interval_seconds)


async def _run_job(job: dict, semaphore: asyncio.Semaphore) -> None:
    job_type = str(job.get("type", ""))
    job_id = str(job.get("id", ""))
    started = time.monotonic()
    handler = handlers.get(job_type)
    if handler is None:
        log.warning(
            "job finished job_type=%s id=%s duration_ms=%s outcome=dropped",
            job_type,
            job_id,
            int((time.monotonic() - started) * 1000),
        )
        return
    outcome = "ok"
    async with semaphore:
        try:
            await handler(job_id, str(job.get("tenant_id", "")))
        except Exception:
            outcome = "error"
            log.warning("job handler failed job_type=%s id=%s", job_type, job_id)
        finally:
            log.info(
                "job finished job_type=%s id=%s duration_ms=%s outcome=%s",
                job_type,
                job_id,
                int((time.monotonic() - started) * 1000),
                outcome,
            )


async def run_worker(
    stop: asyncio.Event | None = None,
    concurrency: int | None = None,
) -> None:
    stop = stop or asyncio.Event()
    semaphore = asyncio.Semaphore(concurrency or settings.worker_concurrency)
    in_flight: set[asyncio.Task] = set()
    reconcile_task = asyncio.create_task(_reconcile_loop())
    try:
        while not stop.is_set():
            if in_flight and len(in_flight) >= (concurrency or settings.worker_concurrency):
                _, in_flight = await asyncio.wait(
                    in_flight,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                continue
            try:
                job = await dequeue(timeout_seconds=5)
            except Exception:
                log.warning("job dequeue failed")
                await asyncio.sleep(1)
                continue
            if job is None:
                continue
            if stop.is_set():
                break
            task = asyncio.create_task(_run_job(job, semaphore))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)
    finally:
        reconcile_task.cancel()
        await asyncio.gather(reconcile_task, return_exceptions=True)
        if in_flight:
            _, pending = await asyncio.wait(in_flight, timeout=30)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        log.info("worker interrupted; shutting down")


if __name__ == "__main__":
    main()
