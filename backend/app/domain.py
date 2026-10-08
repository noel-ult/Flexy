"""Domain constants shared by the API, workers, and storage adapters."""

from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    ANALYZING = "analyzing"
    READY = "ready"
    UNSUPPORTED = "unsupported"
    BUILD_QUEUED = "build_queued"
    BUILDING = "building"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ENVIRONMENT_UNAVAILABLE = "environment_unavailable"
    EXPIRED = "expired"


class ArtifactKind(StrEnum):
    PACKAGE = "artifact"
    REPORT = "report"


class VerificationState(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    UNVERIFIED = "unverified"


SUPPORTED_TARGET = ("arch", "x86_64")

TERMINAL_STATUSES = {
    JobStatus.READY,
    JobStatus.UNSUPPORTED,
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.ENVIRONMENT_UNAVAILABLE,
    JobStatus.EXPIRED,
}

BUILDABLE_STATUSES = {JobStatus.READY}
