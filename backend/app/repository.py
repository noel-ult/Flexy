"""Transactional job persistence and authorization helpers."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings, get_settings
from .domain import ArtifactKind, JobStatus
from .models import DownloadToken, Job, JobLog
from .security import new_job_id, new_secret, secret_matches, token_hash


class NotFoundError(LookupError):
    pass


class AccessDeniedError(PermissionError):
    pass


class InvalidJobStateError(RuntimeError):
    pass


class JobRepository:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings | None = None):
        self.sessions = sessions
        self.settings = settings or get_settings()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _is_expired(value: datetime, now: datetime) -> bool:
        # SQLite does not round-trip timezone information even for timezone=True.
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return normalized < now

    def create_job(
        self,
        *,
        job_id: str | None = None,
        target_os: str,
        target_arch: str,
        original_filename: str,
        upload_key: str,
        upload_sha256: str,
        upload_size: int,
    ) -> tuple[Job, str]:
        capability = new_secret()
        now = self._now()
        job = Job(
            id=job_id or new_job_id(),
            capability_hash=token_hash(capability),
            target_os=target_os,
            target_arch=target_arch,
            original_filename=original_filename,
            upload_key=upload_key,
            upload_sha256=upload_sha256,
            upload_size=upload_size,
            status=JobStatus.ANALYZING.value,
            expires_at=now + timedelta(hours=self.settings.job_retention_hours),
            verification_json={
                "packageCreation": {"state": "not_run"},
                "installation": {"state": "not_run"},
                "launch": {"state": "not_run"},
                "functionality": {"state": "not_run"},
            },
        )
        with self.sessions.begin() as session:
            session.add(job)
            self._append_log_in_session(
                session, job.id, "info", "Upload accepted; analysis queued."
            )
        return job, capability

    def get(self, job_id: str) -> Job:
        with self.sessions() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFoundError("Job not found")
            session.expunge(job)
            return job

    def get_owned(self, job_id: str, capability: str | None) -> Job:
        job = self.get(job_id)
        if not secret_matches(capability, job.capability_hash):
            raise AccessDeniedError("A valid job capability is required")
        return job

    def append_log(self, job_id: str, level: str, message: str) -> None:
        safe_message = message.replace("\x00", "")[:8_000]
        with self.sessions.begin() as session:
            if session.get(Job, job_id) is None:
                raise NotFoundError("Job not found")
            self._append_log_in_session(session, job_id, level, safe_message)

    def _append_log_in_session(
        self, session: Session, job_id: str, level: str, message: str
    ) -> None:
        sequence = session.scalar(
            select(func.coalesce(func.max(JobLog.sequence), 0) + 1).where(JobLog.job_id == job_id)
        )
        session.add(
            JobLog(job_id=job_id, sequence=int(sequence or 1), level=level, message=message)
        )

    def logs_after(self, job_id: str, after: int = 0) -> list[JobLog]:
        with self.sessions() as session:
            rows = list(
                session.scalars(
                    select(JobLog)
                    .where(JobLog.job_id == job_id, JobLog.sequence > max(0, after))
                    .order_by(JobLog.sequence)
                )
            )
            for row in rows:
                session.expunge(row)
            return rows

    def set_analysis(
        self,
        job_id: str,
        *,
        analysis: dict[str, Any],
        blockers: list[dict[str, Any]],
        recipe_id: str | None,
        report_key: str,
        status: JobStatus,
    ) -> None:
        if status not in {JobStatus.READY, JobStatus.UNSUPPORTED, JobStatus.FAILED}:
            raise ValueError("Analysis can only settle to ready, unsupported, or failed")
        with self.sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFoundError("Job not found")
            job.analysis_json = analysis
            job.blockers_json = blockers
            job.recipe_id = recipe_id
            job.report_key = report_key
            job.status = status.value
            if status == JobStatus.FAILED:
                job.completed_at = self._now()
            self._append_log_in_session(
                session,
                job_id,
                "info" if status == JobStatus.READY else "warning",
                f"Analysis completed with status: {status.value}.",
            )

    def mark_error(
        self,
        job_id: str,
        *,
        code: str,
        message: str,
        status: JobStatus = JobStatus.FAILED,
    ) -> None:
        with self.sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFoundError("Job not found")
            job.status = status.value
            job.error_code = code[:96]
            job.error_message = message[:8_000]
            job.completed_at = self._now() if status != JobStatus.BUILDING else None
            self._append_log_in_session(session, job_id, "error", message)

    def queue_build(self, job_id: str) -> Job:
        """Atomically transition a recipe-approved job to build_queued."""
        with self.sessions.begin() as session:
            result = session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status == JobStatus.READY.value,
                    Job.recipe_id.is_not(None),
                )
                .values(status=JobStatus.BUILD_QUEUED.value, build_started_at=self._now())
            )
            if result.rowcount != 1:
                raise InvalidJobStateError("This job is not ready for a build")
            self._append_log_in_session(session, job_id, "info", "Build queued.")
            job = session.get(Job, job_id)
            assert job is not None
            session.flush()
            session.expunge(job)
            return job

    def begin_build(self, job_id: str) -> bool:
        with self.sessions.begin() as session:
            result = session.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == JobStatus.BUILD_QUEUED.value)
                .values(status=JobStatus.BUILDING.value)
            )
            if result.rowcount:
                self._append_log_in_session(
                    session, job_id, "info", "Isolated build environment started."
                )
                return True
            return False

    def complete_build(
        self,
        job_id: str,
        *,
        package_key: str | None,
        verification: dict[str, Any],
        status: JobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if status not in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.ENVIRONMENT_UNAVAILABLE,
        }:
            raise ValueError("Invalid completion status")
        with self.sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFoundError("Job not found")
            job.package_key = package_key
            job.verification_json = verification
            job.status = status.value
            job.error_code = error_code
            job.error_message = error_message
            job.completed_at = self._now()
            message = (
                "Build completed."
                if status == JobStatus.SUCCEEDED
                else (error_message or "Build failed.")
            )
            self._append_log_in_session(
                session, job_id, "info" if status == JobStatus.SUCCEEDED else "error", message
            )

    def mint_download_token(self, job_id: str, kind: ArtifactKind) -> tuple[str, datetime]:
        token = new_secret()
        expires_at = self._now() + timedelta(seconds=self.settings.download_token_ttl_seconds)
        with self.sessions.begin() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFoundError("Job not found")
            key = job.package_key if kind == ArtifactKind.PACKAGE else job.report_key
            if not key:
                raise InvalidJobStateError("Requested artifact is not available")
            session.add(
                DownloadToken(
                    job_id=job_id,
                    token_hash=token_hash(token),
                    artifact_kind=kind.value,
                    expires_at=expires_at,
                )
            )
        return token, expires_at

    def consume_download_token(self, token: str) -> tuple[Job, ArtifactKind]:
        now = self._now()
        with self.sessions.begin() as session:
            grant = session.scalar(
                select(DownloadToken).where(DownloadToken.token_hash == token_hash(token))
            )
            if (
                grant is None
                or grant.used_at is not None
                or self._is_expired(grant.expires_at, now)
            ):
                raise AccessDeniedError("Download token is invalid or expired")
            consumed = session.execute(
                update(DownloadToken)
                .where(DownloadToken.id == grant.id, DownloadToken.used_at.is_(None))
                .values(used_at=now)
            )
            if consumed.rowcount != 1:
                raise AccessDeniedError("Download token is invalid or expired")
            job = session.get(Job, grant.job_id)
            if job is None or self._is_expired(job.expires_at, now):
                raise AccessDeniedError("Download is no longer available")
            kind = ArtifactKind(grant.artifact_kind)
            session.expunge(job)
            return job, kind

    def expired_jobs(self, now: datetime | None = None) -> list[Job]:
        now = now or self._now()
        with self.sessions() as session:
            jobs = list(session.scalars(select(Job).where(Job.expires_at < now)))
            for job in jobs:
                session.expunge(job)
            return jobs

    def delete_jobs(self, ids: Iterable[str]) -> int:
        identifiers = list(ids)
        if not identifiers:
            return 0
        with self.sessions.begin() as session:
            result = session.execute(delete(Job).where(Job.id.in_(identifiers)))
            return int(result.rowcount or 0)
