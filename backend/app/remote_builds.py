"""One authenticated HTTPS-pull native worker; no cloud credentials enter the VM.

The singleton UPDATE is a write lock on both PostgreSQL and SQLite. Every queue,
claim, lease, log and completion operation serialises against it. Native work is
never placed on the inspection worker's Dramatiq queue.
"""

from __future__ import annotations

import base64
import binascii
import hmac
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import func, select, update

from .domain import JobStatus
from .models import Job, RemoteWorker
from .native_artifact import MAX_NATIVE_BYTES, validate_native_artifact
from .security import new_secret, secret_matches, token_hash
from .services import _compatibility_report, _not_run_verification, package_key

SUPPORTED_RECIPE = "flexy-demo-1.0.0-amd64-arch-x86_64"
CHECKS = ("packageCreation", "installation", "launch", "functionality")
router = APIRouter(prefix="/v1/native-worker")


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@contextmanager
def locked(container):
    with container.repository.sessions.begin() as session:
        result = session.execute(
            update(RemoteWorker)
            .where(RemoteWorker.id == 1)
            .values(revision=RemoteWorker.revision + 1)
        )
        if result.rowcount != 1:
            raise HTTPException(503, "Native worker migration is required.")
        worker = session.get(RemoteWorker, 1)
        assert worker is not None
        yield session, worker


def clear_lease(worker):
    worker.job_id = worker.lease_hash = worker.lease_expires = None


def report(container, job):
    if job.report_key:
        import json

        value = _compatibility_report(
            job.id,
            job.analysis_json or {},
            job.blockers_json or [],
            job.recipe_id,
            (job.analysis_json or {}).get("recipe", {}).get("mappedDependencies", []),
            job.verification_json,
            {"code": job.error_code, "message": job.error_message} if job.error_code else None,
        )
        container.store.put_bytes(
            job.report_key, json.dumps(value, indent=2).encode(), "application/json"
        )


def expire_locked(container, session, worker):
    now = datetime.now(UTC)
    ids = []
    if worker.job_id and (not worker.lease_expires or utc(worker.lease_expires) <= now):
        ids.append(worker.job_id)
        clear_lease(worker)
    waiting = session.scalars(
        select(Job).where(
            Job.status == JobStatus.BUILD_QUEUED.value,
            Job.build_started_at < now - timedelta(seconds=120),
        )
    ).all()
    ids.extend(job.id for job in waiting)
    for job_id in ids:
        job = session.get(Job, job_id)
        if job and job.status in {"building", "build_queued"}:
            job.status = JobStatus.ENVIRONMENT_UNAVAILABLE.value
            job.error_code = "native_worker_timeout"
            job.error_message = (
                "Laptop worker disconnected or exceeded its lease; no success claimed."
            )
            job.verification_json = _not_run_verification(job.error_message)
            job.completed_at = now
            container.repository._append_log_in_session(
                session, job.id, "warning", job.error_message
            )
            report(container, job)


def sweep(container):
    if container.settings.build_executor == "remote":
        with locked(container) as (session, worker):
            expire_locked(container, session, worker)


def queue_native(container, job_id):
    with locked(container) as (session, worker):
        expire_locked(container, session, worker)
        if not worker.last_seen or utc(worker.last_seen) < datetime.now(UTC) - timedelta(
            seconds=20
        ):
            raise HTTPException(
                503, "Laptop Arch worker is offline. Start the VM and worker bridge."
            )
        count = session.scalar(
            select(func.count())
            .select_from(Job)
            .where(Job.status.in_(["build_queued", "building"]))
        )
        if count >= 3:
            raise HTTPException(429, "Native build queue is full; try again later.")
        job = session.get(Job, job_id)
        if not job or job.status != "ready" or job.recipe_id != SUPPORTED_RECIPE:
            raise HTTPException(409, "Only the exact reviewed demo recipe can be queued.")
        if utc(job.expires_at) <= datetime.now(UTC) or job.upload_size > MAX_NATIVE_BYTES:
            raise HTTPException(409, "Source expired or exceeds the native worker limit.")
        job.status = "build_queued"
        job.build_started_at = datetime.now(UTC)
        container.repository._append_log_in_session(
            session, job.id, "info", "Native build queued for the laptop's offline Arch VM."
        )


def authorised(request, authorization):
    container = request.app.state.container
    expected = container.settings.remote_build_token
    if container.settings.build_executor != "remote" or not expected or len(expected) < 32:
        raise HTTPException(503, "Native worker is not configured.")
    if not authorization or not hmac.compare_digest(
        authorization.encode("utf-8"), ("Bearer " + expected).encode("utf-8")
    ):
        raise HTTPException(401, "Worker authentication required.")
    return container


def lease_job(session, worker, job_id, lease):
    job = session.get(Job, job_id)
    if (
        worker.job_id != job_id
        or not worker.lease_hash
        or not secret_matches(lease, worker.lease_hash)
        or not worker.lease_expires
        or utc(worker.lease_expires) <= datetime.now(UTC)
        or not job
        or job.status != "building"
        or utc(job.expires_at) <= datetime.now(UTC)
    ):
        raise HTTPException(404, "Active lease not found.")
    return job


@router.post("/poll")
def poll(request: Request, authorization: str | None = Header(default=None)):
    container = authorised(request, authorization)
    with locked(container) as (session, worker):
        expire_locked(container, session, worker)
        worker.last_seen = datetime.now(UTC)
        if worker.job_id:
            return {"job": None}
        job = session.scalars(
            select(Job)
            .where(
                Job.status == "build_queued",
                Job.recipe_id == SUPPORTED_RECIPE,
                Job.expires_at > datetime.now(UTC),
                Job.upload_size <= MAX_NATIVE_BYTES,
            )
            .order_by(Job.build_started_at)
            .limit(1)
        ).first()
        if job is None:
            return {"job": None}
        secret = new_secret()
        worker.job_id = job.id
        worker.lease_hash = token_hash(secret)
        worker.lease_expires = datetime.now(UTC) + timedelta(seconds=120)
        job.status = "building"
        container.repository._append_log_in_session(
            session,
            job.id,
            "info",
            "Offline Arch VM claimed the job; 30-second native build limit.",
        )
        return {
            "job": {
                "id": job.id,
                "lease": secret,
                "sha256": job.upload_sha256,
                "size": job.upload_size,
                "recipeId": job.recipe_id,
            }
        }


@router.get("/{job_id}/source")
def source(
    request: Request,
    job_id: str,
    authorization: str | None = Header(default=None),
    x_build_lease: str | None = Header(default=None),
):
    from fastapi.responses import Response

    container = authorised(request, authorization)
    with locked(container) as (session, worker):
        job = lease_job(session, worker, job_id, x_build_lease)
        with container.store.open(job.upload_key) as stream:
            data = stream.read(MAX_NATIVE_BYTES + 1)
        if len(data) != job.upload_size or len(data) > MAX_NATIVE_BYTES:
            raise HTTPException(409, "Stored source size differs from approved upload.")
        return Response(data, media_type="application/vnd.debian.binary-package")


@router.post("/{job_id}/log")
def log(
    request: Request,
    job_id: str,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_build_lease: str | None = Header(default=None),
):
    container = authorised(request, authorization)
    message = body.get("message")
    if not isinstance(message, str) or len(message.encode()) > 4096:
        raise HTTPException(422, "Log entry exceeds limit.")
    message = message.replace("\x00", "")
    with locked(container) as (session, worker):
        lease_job(session, worker, job_id, x_build_lease)
        from .models import JobLog

        used = sum(
            len(item.encode("utf-8"))
            for item in session.scalars(select(JobLog.message).where(JobLog.job_id == job_id))
        )
        if used + len(message.encode("utf-8")) > min(container.settings.log_limit_bytes, 65536):
            raise HTTPException(413, "Job log budget exceeded.")
        container.repository._append_log_in_session(session, job_id, "info", message)
    return {"accepted": True}


@router.post("/{job_id}/complete")
def complete(
    request: Request,
    job_id: str,
    body: dict[str, Any],
    authorization: str | None = Header(default=None),
    x_build_lease: str | None = Header(default=None),
):
    container = authorised(request, authorization)
    verification = body.get("verification")
    if not isinstance(verification, dict) or set(verification) != set(CHECKS):
        raise HTTPException(422, "All four separate verification results are required.")
    for check in verification.values():
        if (
            not isinstance(check, dict)
            or check.get("state") not in {"passed", "failed", "not_run", "unverified"}
            or set(check) - {"state", "detail", "output"}
            or any(not isinstance(v, str) or len(v) > 8192 for v in check.values())
        ):
            raise HTTPException(422, "Invalid bounded verification evidence.")
    encoded = body.get("package")
    data = None
    if encoded is not None:
        if not isinstance(encoded, str) or len(encoded) > 4 * (MAX_NATIVE_BYTES // 3 + 1):
            raise HTTPException(413, "Package exceeds native worker limit.")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise HTTPException(422, "Invalid package encoding.") from error
    with locked(container) as (session, worker):
        job = lease_job(session, worker, job_id, x_build_lease)
        recipe = container.recipes.get(job.recipe_id)
        if recipe is None or recipe.identifier != SUPPORTED_RECIPE:
            raise HTTPException(409, "Approved recipe is unavailable.")
        if data is not None:
            try:
                validate_native_artifact(data, recipe)
            except Exception as error:
                raise HTTPException(
                    422, "Returned package failed static recipe validation."
                ) from error
            if verification["packageCreation"]["state"] != "passed":
                raise HTTPException(422, "Package creation evidence is inconsistent.")
            for name, expected in (
                ("launch", recipe.expected_launch_output),
                ("functionality", recipe.expected_functionality_output),
            ):
                if verification[name]["state"] == "passed" and (
                    verification["installation"]["state"] != "passed"
                    or verification[name].get("output", "").strip() != expected
                ):
                    raise HTTPException(422, "Native execution evidence differs from recipe.")
            job.package_key = package_key(
                job.id, f"{recipe.package}-{recipe.version}-{recipe.pkgrel}-x86_64.pkg.tar.zst"
            )
            container.store.put_bytes(job.package_key, data, "application/zstd")
        elif any(check["state"] == "passed" for check in verification.values()):
            raise HTTPException(422, "Cannot claim success without a returned package.")
        job.verification_json = verification
        job.status = "succeeded" if data else "failed"
        job.error_code = None if data else "native_build_failed"
        job.error_message = (
            None if data else "Offline native build failed; see verification and logs."
        )
        job.completed_at = datetime.now(UTC)
        container.repository._append_log_in_session(
            session,
            job.id,
            "info",
            "Native VM result received and package validated. "
            "CLI checks do not prove desktop compatibility."
            if data
            else "Native build failed.",
        )
        report(container, job)
        clear_lease(worker)
    return {"accepted": True}
