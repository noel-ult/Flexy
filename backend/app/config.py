"""Configuration with secure, deliberately small defaults.

The production deployment is expected to provide all storage/database secrets through
its secret manager.  Local defaults intentionally use a SQLite database and a local
artifact directory so the demo can be run without cloud credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be positive")
    return parsed


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    redis_url: str
    s3_endpoint: str | None
    s3_bucket: str | None
    s3_access_key: str | None
    s3_secret_key: str | None
    artifact_backend: str
    upload_dir: Path
    artifact_dir: Path
    work_dir: Path
    recipe_dir: Path
    app_base_url: str
    frontend_origins: tuple[str, ...]
    queue_mode: str
    build_executor: str
    bwrap_path: str
    max_upload_bytes: int
    max_extraction_bytes: int
    max_file_count: int
    job_timeout_seconds: int
    job_retention_hours: int
    download_token_ttl_seconds: int
    log_limit_bytes: int
    allow_demo_inline_queue: bool
    auto_create_schema: bool
    kubernetes_builder_image: str | None
    cleanup_interval_seconds: int
    s3_server_side_encryption: str = "AES256"
    remote_build_token: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        backend_root = Path(__file__).resolve().parents[1]
        default_data = backend_root / "data"
        recipe_dir = Path(os.getenv("FLEXY_RECIPE_DIR", backend_root / "fixtures" / "recipes"))
        origins = tuple(
            item.strip()
            for item in os.getenv("FRONTEND_ORIGIN", "http://localhost:3000").split(",")
            if item.strip()
        )
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./flexy.db"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            s3_endpoint=os.getenv("S3_ENDPOINT") or None,
            s3_bucket=os.getenv("S3_BUCKET") or None,
            s3_access_key=os.getenv("S3_ACCESS_KEY") or None,
            s3_secret_key=os.getenv("S3_SECRET_KEY") or None,
            artifact_backend=os.getenv("ARTIFACT_BACKEND", "local").strip().lower(),
            upload_dir=Path(os.getenv("UPLOAD_DIR", default_data / "uploads")),
            artifact_dir=Path(os.getenv("ARTIFACT_DIR", default_data / "artifacts")),
            work_dir=Path(os.getenv("WORK_DIR", default_data / "work")),
            recipe_dir=recipe_dir,
            app_base_url=os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/"),
            frontend_origins=origins,
            queue_mode=os.getenv("QUEUE_MODE", "dramatiq").strip().lower(),
            # A source-only process runs directly on a developer workstation.
            # Keep it fail-closed unless Compose/Kubernetes explicitly selects a
            # dedicated isolated runner image.
            build_executor=os.getenv("BUILD_EXECUTOR", "unavailable").strip().lower(),
            bwrap_path=os.getenv("BWRAP_PATH", "/usr/bin/bwrap"),
            max_upload_bytes=_int_env("MAX_UPLOAD_BYTES", 50 * 1024 * 1024),
            max_extraction_bytes=_int_env("MAX_EXTRACTION_BYTES", 250 * 1024 * 1024),
            max_file_count=_int_env("MAX_FILE_COUNT", 10_000),
            job_timeout_seconds=_int_env("JOB_TIMEOUT_SECONDS", 300),
            job_retention_hours=_int_env("JOB_RETENTION_HOURS", 24),
            download_token_ttl_seconds=_int_env("DOWNLOAD_TOKEN_TTL_SECONDS", 15 * 60),
            log_limit_bytes=_int_env("LOG_LIMIT_BYTES", 1 * 1024 * 1024),
            allow_demo_inline_queue=_bool_env("ALLOW_DEMO_INLINE_QUEUE", False),
            auto_create_schema=_bool_env("AUTO_CREATE_SCHEMA", True),
            kubernetes_builder_image=os.getenv("FLEXY_BUILDER_IMAGE") or None,
            cleanup_interval_seconds=_int_env("CLEANUP_INTERVAL_SECONDS", 60 * 60),
            s3_server_side_encryption=os.getenv("S3_SERVER_SIDE_ENCRYPTION", "AES256"),
            remote_build_token=os.getenv("REMOTE_BUILD_TOKEN") or None,
        )

    def ensure_local_directories(self) -> None:
        """Create only service-owned directories, never a directory from an upload."""
        for directory in (self.upload_dir, self.artifact_dir, self.work_dir):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def reset_settings_for_tests() -> None:
    """Test-only helper; production code must treat settings as immutable."""
    global _settings
    _settings = None
