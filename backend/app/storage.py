"""Private artifact storage adapters.

The web API always streams artifacts itself after authorizing a short-lived grant;
this module never produces public/presigned object URLs.
"""

from __future__ import annotations

import io
import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .config import Settings, get_settings


class ArtifactStoreError(RuntimeError):
    pass


def _validate_key(key: str) -> str:
    path = PurePosixPath(key)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ArtifactStoreError("Unsafe artifact key")
    return str(path)


class ArtifactStore(ABC):
    @abstractmethod
    def put_file(self, key: str, source: Path, content_type: str | None = None) -> None: ...

    @abstractmethod
    def open(self, key: str) -> BinaryIO: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...

    def put_bytes(self, key: str, content: bytes, content_type: str | None = None) -> None:
        with io.BytesIO(content) as source:
            self.put_stream(key, source, content_type)

    def put_stream(self, key: str, source: BinaryIO, content_type: str | None = None) -> None:
        """Default stream implementation uses a securely-created temporary local file."""
        import tempfile

        with tempfile.NamedTemporaryFile(prefix="flexy-store-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            shutil.copyfileobj(source, temporary)
        try:
            self.put_file(key, temporary_path, content_type)
        finally:
            temporary_path.unlink(missing_ok=True)

    def materialize(self, key: str, destination: Path) -> Path:
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.open(key) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)
        os.chmod(destination, 0o600)
        return destination


class LocalArtifactStore(ArtifactStore):
    """Private filesystem store for local development and integration tests."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        valid_key = _validate_key(key)
        candidate = (self.root / valid_key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ArtifactStoreError("Artifact key escapes storage root")
        return candidate

    def put_file(self, key: str, source: Path, content_type: str | None = None) -> None:
        destination = self._path(key)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Copy rather than move: callers own a short-lived, untrusted staging file.
        with source.open("rb") as input_file, destination.open("wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
        os.chmod(destination, 0o600)

    def open(self, key: str) -> BinaryIO:
        try:
            return self._path(key).open("rb")
        except FileNotFoundError as exc:
            raise ArtifactStoreError("Artifact does not exist") from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        directory = self._path(prefix)
        if directory == self.root:
            raise ArtifactStoreError("Refusing to remove storage root")
        if directory.exists():
            shutil.rmtree(directory)


class S3ArtifactStore(ArtifactStore):
    """S3-compatible private store (MinIO works for local deployment)."""

    def __init__(self, settings: Settings):
        if not settings.s3_bucket:
            raise ArtifactStoreError("S3_BUCKET is required when ARTIFACT_BACKEND=s3")
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - dependency validation
            raise ArtifactStoreError("boto3 is required for S3 artifact storage") from exc
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    def put_file(self, key: str, source: Path, content_type: str | None = None) -> None:
        args: dict[str, str] = {"ServerSideEncryption": "AES256"}
        if content_type:
            args["ContentType"] = content_type
        self.client.upload_file(str(source), self.bucket, _validate_key(key), ExtraArgs=args)

    def put_stream(self, key: str, source: BinaryIO, content_type: str | None = None) -> None:
        args: dict[str, str] = {"ServerSideEncryption": "AES256"}
        if content_type:
            args["ContentType"] = content_type
        self.client.upload_fileobj(source, self.bucket, _validate_key(key), ExtraArgs=args)

    def open(self, key: str) -> BinaryIO:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=_validate_key(key))
        except Exception as exc:
            if _is_s3_not_found(exc):
                raise ArtifactStoreError("Artifact does not exist") from exc
            raise
        return response["Body"]

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=_validate_key(key))
            return True
        except Exception as error:
            if _is_s3_not_found(error):
                return False
            raise

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=_validate_key(key))

    def delete_prefix(self, prefix: str) -> None:
        prefix = _validate_key(prefix).rstrip("/") + "/"
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objects = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if objects:
                self.client.delete_objects(
                    Bucket=self.bucket, Delete={"Objects": objects, "Quiet": True}
                )


def create_artifact_store(settings: Settings | None = None) -> ArtifactStore:
    settings = settings or get_settings()
    if settings.artifact_backend == "local":
        return LocalArtifactStore(settings.artifact_dir)
    if settings.artifact_backend == "s3":
        return S3ArtifactStore(settings)
    raise ArtifactStoreError(f"Unsupported ARTIFACT_BACKEND: {settings.artifact_backend}")


def _is_s3_not_found(error: Exception) -> bool:
    response = getattr(error, "response", None)
    code = response.get("Error", {}).get("Code") if isinstance(response, dict) else None
    return str(code) in {"404", "NoSuchKey", "NotFound", "NoSuchBucket"}
