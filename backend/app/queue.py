"""Queue dispatch with an explicitly opt-in local development fallback."""

from __future__ import annotations

import threading

from .config import get_settings


class QueueUnavailable(RuntimeError):
    pass


def enqueue_analysis(job_id: str) -> None:
    _enqueue("analysis", job_id)


def enqueue_build(job_id: str) -> None:
    _enqueue("build", job_id)


def _enqueue(kind: str, job_id: str) -> None:
    settings = get_settings()
    if settings.queue_mode == "inline" and settings.allow_demo_inline_queue:
        from .services import process_analysis, process_build

        target = process_analysis if kind == "analysis" else process_build
        threading.Thread(
            target=target, args=(job_id,), daemon=True, name=f"flexy-{kind}-{job_id[:8]}"
        ).start()
        return
    if settings.queue_mode != "dramatiq":
        raise QueueUnavailable("A Redis/Dramatiq queue is required; inline execution is disabled.")
    try:
        from .worker import analyze_job, build_job

        (analyze_job if kind == "analysis" else build_job).send(job_id)
    except Exception as exc:
        raise QueueUnavailable("Could not enqueue job to the isolated worker queue.") from exc
