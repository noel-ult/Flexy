"""Dramatiq entrypoint and durable cleanup command.

Run with `dramatiq app.worker` for workers, or `python -m app.worker cleanup`
from a restricted periodic scheduler for retention cleanup.
"""

from __future__ import annotations

import sys

from .config import get_settings
from .services import cleanup_expired_jobs, process_analysis, process_build

try:  # Keep inspection-only tools importable before optional queue dependencies are installed.
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker

    dramatiq.set_broker(RedisBroker(url=get_settings().redis_url))

    @dramatiq.actor(max_retries=2, min_backoff=5_000, max_backoff=60_000, time_limit=330_000)
    def analyze_job(job_id: str) -> None:
        process_analysis(job_id)

    @dramatiq.actor(max_retries=0, time_limit=330_000)
    def build_job(job_id: str) -> None:
        process_build(job_id)

    @dramatiq.actor(max_retries=0)
    def cleanup_expired_jobs_task() -> int:
        return cleanup_expired_jobs()

except ImportError:  # pragma: no cover - exercised only before dependencies are installed
    class _UnavailableActor:
        def __init__(self, function):
            self.function = function

        def send(self, *_args, **_kwargs):
            raise RuntimeError("dramatiq is not installed")

    analyze_job = _UnavailableActor(process_analysis)
    build_job = _UnavailableActor(process_build)
    cleanup_expired_jobs_task = _UnavailableActor(cleanup_expired_jobs)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "cleanup":
        print(f"Removed {cleanup_expired_jobs()} expired job(s).")
        return 0
    print("Usage: python -m app.worker cleanup", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
