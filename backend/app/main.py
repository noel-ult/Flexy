"""FastAPI HTTP boundary for the remote conversion service."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text

from .config import Settings, get_settings
from .db import initialize_local_schema
from .domain import SUPPORTED_TARGET, ArtifactKind, JobStatus
from .queue import QueueUnavailable, enqueue_analysis, enqueue_build
from .repository import AccessDeniedError, InvalidJobStateError, JobRepository, NotFoundError
from .security import new_job_id, safe_download_filename
from .services import ServiceContainer, cleanup_expired_jobs, upload_key
from .storage import ArtifactStoreError


class UploadBodyTooLarge(Exception):
    pass


class UploadBodyLimitMiddleware:
    """Reject oversized multipart bodies before Starlette parses/spools them."""

    def __init__(self, app: Any, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        is_upload = (
            scope.get("type") == "http"
            and scope.get("path") == "/v1/jobs"
            and scope.get("method") == "POST"
        )
        if not is_upload:
            await self.app(scope, receive, send)
            return
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        content_length = headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > self.max_bytes:
            await JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Upload is too large."},
            )(scope, receive, send)
            return
        seen = 0

        async def limited_receive() -> Any:
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    raise UploadBodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except UploadBodyTooLarge:
            # Multipart parsing occurs before an endpoint response begins, so this
            # is a clean 413 rather than a partially-written application response.
            await JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={"detail": "Upload is too large."},
            )(scope, receive, send)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value else None


def _job_response(job: Any, repository: JobRepository | None = None) -> dict[str, Any]:
    blockers = job.blockers_json or []
    logs: list[dict[str, Any]] = []
    if repository is not None:
        logs = [
            {
                "sequence": item.sequence,
                "level": item.level,
                "message": item.message,
                "createdAt": _iso(item.created_at),
            }
            for item in repository.logs_after(
                job.id, max(0, _last_log_sequence(repository, job.id) - 200)
            )
        ]
    package_name = (job.analysis_json or {}).get("package", {}).get("name")
    package_version = (job.analysis_json or {}).get("package", {}).get("version")
    artifacts: list[dict[str, Any]] = []
    if job.package_key:
        artifacts.append(
            {
                "kind": "artifact",
                "name": Path(job.package_key).name,
                "available": True,
            }
        )
    if job.report_key:
        artifacts.append({"kind": "report", "name": "compatibility-report.json", "available": True})
    return {
        "id": job.id,
        "status": job.status,
        "target": {"os": job.target_os, "architecture": job.target_arch},
        "createdAt": _iso(job.created_at),
        "expiresAt": _iso(job.expires_at),
        "completedAt": _iso(job.completed_at),
        "updatedAt": _iso(job.updated_at),
        "packageName": package_name,
        "packageVersion": package_version,
        "source": {
            "filename": job.original_filename,
            "sha256": job.upload_sha256,
            "size": job.upload_size,
        },
        "analysis": job.analysis_json,
        "blockers": blockers,
        "compatibility": {
            "supported": job.status
            in {
                JobStatus.READY.value,
                JobStatus.BUILD_QUEUED.value,
                JobStatus.BUILDING.value,
                JobStatus.SUCCEEDED.value,
            }
            and not blockers,
            "recipeId": job.recipe_id,
            "blockers": blockers,
            "limitations": [
                "Changing a package format does not make Windows or macOS binaries run on Linux.",
                "A successful build alone does not prove installation, launch, "
                "desktop compatibility, or functionality.",
                "This browser uses a remote conversion service; "
                "downloaded software runs on your computer.",
            ],
        },
        "verification": job.verification_json
        or {
            "packageCreation": {"state": "not_run"},
            "installation": {"state": "not_run"},
            "launch": {"state": "not_run"},
            "functionality": {"state": "not_run"},
        },
        "artifacts": artifacts,
        "error": (
            {"code": job.error_code, "message": job.error_message}
            if job.error_code or job.error_message
            else None
        ),
        "logs": logs,
    }


def _last_log_sequence(repository: JobRepository, job_id: str) -> int:
    logs = repository.logs_after(job_id, 0)
    return logs[-1].sequence if logs else 0


async def _stream_upload(upload: UploadFile, settings: Settings) -> tuple[Path, int, str]:
    settings.upload_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="incoming-", suffix=".deb", dir=settings.upload_dir)
    path = Path(name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = await upload.read(128 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.max_upload_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Uploads are limited to {settings.max_upload_bytes} bytes.",
                    )
                digest.update(chunk)
                output.write(chunk)
        return path, total, digest.hexdigest()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def _require_owned_job(
    request: Request,
    job_id: str,
    capability: Annotated[str | None, Header(alias="X-Job-Capability")] = None,
) -> tuple[Any, ServiceContainer]:
    container: ServiceContainer = request.app.state.container
    try:
        job = container.repository.get_owned(job_id, capability)
    except (NotFoundError, AccessDeniedError) as exc:
        # Do not turn a guessed job id into an authorization oracle.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found") from exc
    if _as_utc(job.expires_at) < datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_410_GONE, detail="This job has expired and is being deleted."
        )
    return job, container


def _artifact_kind(value: str) -> ArtifactKind:
    if value in {"artifact", "package"}:
        return ArtifactKind.PACKAGE
    if value == "report":
        return ArtifactKind.REPORT
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown artifact")


def _file_iterator(file_object: Any) -> Iterator[bytes]:
    try:
        while chunk := file_object.read(256 * 1024):
            yield chunk
    finally:
        file_object.close()


async def _cleanup_loop(container: ServiceContainer, interval_seconds: int) -> None:
    """Durable cleanup is also available as a worker command; this covers idle dev stacks."""
    while True:
        try:
            await asyncio.to_thread(cleanup_expired_jobs, container)
        except Exception:
            # Retain metadata when object deletion failed; a later loop/worker retry
            # can safely retry because cleanup is idempotent.
            pass
        await asyncio.sleep(interval_seconds)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured_settings = settings or get_settings()
    api_container = ServiceContainer.create(configured_settings)

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        configured_settings.ensure_local_directories()
        container: ServiceContainer = instance.state.container
        if configured_settings.auto_create_schema:
            initialize_local_schema(container.repository.sessions)
        cleanup_task = asyncio.create_task(
            _cleanup_loop(container, configured_settings.cleanup_interval_seconds),
            name="flexy-retention-cleanup",
        )
        try:
            yield
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(
        title="Flexy API",
        version="0.1.0",
        description=(
            "Safe, recipe-based remote package conversion. "
            "It does not promise universal compatibility."
        ),
        lifespan=lifespan,
    )
    app.state.container = api_container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(configured_settings.frontend_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Job-Capability"],
        expose_headers=["Content-Disposition"],
    )
    app.add_middleware(
        UploadBodyLimitMiddleware,
        max_bytes=configured_settings.max_upload_bytes + 256 * 1024,
    )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            container: ServiceContainer = app.state.container
            with container.repository.sessions() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "unavailable"}
            )
        return JSONResponse(content={"status": "ready"})

    @app.post("/v1/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        request: Request,
        package: Annotated[UploadFile, File(...)],
        target_os: Annotated[str, Form(...)],
        target_arch: Annotated[str, Form(...)],
    ) -> dict[str, Any]:
        if (target_os, target_arch) != SUPPORTED_TARGET:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Only Arch Linux x86_64 is available in this release.",
            )
        filename = safe_download_filename(package.filename or "upload.deb", "upload.deb")
        if not filename.lower().endswith(".deb"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Upload a Debian .deb package.",
            )
        content_length = request.headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > configured_settings.max_upload_bytes + 128 * 1024
        ):
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large."
            )
        temporary, size, digest = await _stream_upload(package, configured_settings)
        job_id = new_job_id()
        container: ServiceContainer = request.app.state.container
        try:
            job, capability = container.repository.create_job(
                job_id=job_id,
                target_os=target_os,
                target_arch=target_arch,
                original_filename=filename,
                upload_key=upload_key(job_id),
                upload_sha256=digest,
                upload_size=size,
            )
            try:
                container.store.put_file(
                    job.upload_key, temporary, "application/vnd.debian.binary-package"
                )
            except ArtifactStoreError:
                container.repository.mark_error(
                    job.id,
                    code="storage_unavailable",
                    message="Upload storage is unavailable; no analysis was queued.",
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "storage_unavailable",
                        "message": "Upload storage is unavailable. Check the storage service, "
                        "credentials and encryption configuration. No analysis was queued.",
                    },
                ) from None
            try:
                enqueue_analysis(job.id)
            except QueueUnavailable as error:
                container.repository.mark_error(
                    job.id,
                    code="queue_unavailable",
                    message="Analysis worker is unavailable; the package was not analyzed.",
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
                ) from error
        finally:
            temporary.unlink(missing_ok=True)
        return {
            "id": job.id,
            "capability": capability,
            "status": job.status,
            "expiresAt": _iso(job.expires_at),
        }

    @app.get("/v1/jobs/{job_id}")
    async def get_job(
        owned: Annotated[tuple[Any, ServiceContainer], Depends(_require_owned_job)],
    ) -> dict[str, Any]:
        job, container = owned
        return _job_response(job, container.repository)

    @app.get("/v1/jobs/{job_id}/events")
    async def job_events(
        owned: Annotated[tuple[Any, ServiceContainer], Depends(_require_owned_job)],
        after: int = 0,
    ) -> StreamingResponse:
        initial_job, container = owned

        async def events() -> AsyncIterator[str]:
            sequence = max(0, after)
            last_status: str | None = None
            job_id = initial_job.id
            while True:
                try:
                    current = container.repository.get(job_id)
                except NotFoundError:
                    yield 'event: end\ndata: {"reason":"deleted"}\n\n'
                    return
                if current.status != last_status:
                    payload = json.dumps(
                        {
                            "id": current.id,
                            "status": current.status,
                            "verification": current.verification_json,
                        }
                    )
                    yield f"event: status\ndata: {payload}\n\n"
                    last_status = current.status
                for item in container.repository.logs_after(job_id, sequence):
                    sequence = item.sequence
                    payload = json.dumps(
                        {
                            "sequence": item.sequence,
                            "level": item.level,
                            "message": item.message,
                            "createdAt": _iso(item.created_at),
                        }
                    )
                    yield f"event: log\ndata: {payload}\n\n"
                if current.status in {
                    JobStatus.READY.value,
                    JobStatus.UNSUPPORTED.value,
                    JobStatus.SUCCEEDED.value,
                    JobStatus.FAILED.value,
                    JobStatus.ENVIRONMENT_UNAVAILABLE.value,
                    JobStatus.EXPIRED.value,
                }:
                    yield f"event: end\ndata: {json.dumps({'status': current.status})}\n\n"
                    return
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.75)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @app.post("/v1/jobs/{job_id}/build", status_code=status.HTTP_202_ACCEPTED)
    async def start_build(
        owned: Annotated[tuple[Any, ServiceContainer], Depends(_require_owned_job)],
    ) -> dict[str, Any]:
        job, container = owned
        try:
            queued = container.repository.queue_build(job.id)
            enqueue_build(job.id)
        except InvalidJobStateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except QueueUnavailable as exc:
            container.repository.mark_error(
                job.id,
                code="queue_unavailable",
                message="Build worker is unavailable; no build was started.",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        return _job_response(container.repository.get(queued.id), container.repository)

    @app.post("/v1/jobs/{job_id}/downloads/{artifact}")
    async def mint_download(
        artifact: str,
        owned: Annotated[tuple[Any, ServiceContainer], Depends(_require_owned_job)],
    ) -> dict[str, Any]:
        job, container = owned
        kind = _artifact_kind(artifact)
        try:
            token, expires_at = container.repository.mint_download_token(job.id, kind)
        except InvalidJobStateError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {
            "artifact": "package" if kind == ArtifactKind.PACKAGE else "report",
            "url": f"/v1/downloads/{token}",
            "expiresAt": _iso(expires_at),
        }

    @app.get("/v1/downloads/{token}")
    async def download(token: str, request: Request) -> StreamingResponse:
        container: ServiceContainer = request.app.state.container
        try:
            job, kind = container.repository.consume_download_token(token)
            object_key = job.package_key if kind == ArtifactKind.PACKAGE else job.report_key
            if not object_key:
                raise ArtifactStoreError("artifact missing")
            file_object = container.store.open(object_key)
        except (AccessDeniedError, NotFoundError, ArtifactStoreError):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Download is unavailable"
            ) from None
        if kind == ArtifactKind.PACKAGE:
            name = safe_download_filename(Path(object_key).name, "converted.pkg.tar.zst")
            media_type = "application/zstd"
        else:
            name = "compatibility-report.json"
            media_type = "application/json"
        return StreamingResponse(
            _file_iterator(file_object),
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return app


app = create_app()
