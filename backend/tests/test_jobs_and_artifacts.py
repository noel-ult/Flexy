from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path

HAS_SQLALCHEMY = find_spec("sqlalchemy") is not None

if HAS_SQLALCHEMY:
    from sqlalchemy import select

    from app.config import Settings
    from app.db import create_session_factory, initialize_local_schema
    from app.domain import ArtifactKind, JobStatus
    from app.models import DownloadToken
    from app.repository import AccessDeniedError, JobRepository
    from app.security import token_hash
    from app.storage import ArtifactStoreError, LocalArtifactStore

@unittest.skipUnless(HAS_SQLALCHEMY, "requires backend runtime dependencies")
class JobAndArtifactTests(unittest.TestCase):
    def _repository(self, temporary: Path):
        settings = replace(
            Settings.from_env(),
            database_url=f"sqlite:///{temporary / 'jobs.sqlite'}",
            artifact_dir=temporary / "artifacts",
            upload_dir=temporary / "uploads",
            work_dir=temporary / "work",
        )
        factory = create_session_factory(settings)
        initialize_local_schema(factory)
        return JobRepository(factory, settings), factory

    def test_capability_guard_and_atomic_build_transition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            repository, _factory = self._repository(Path(temporary_text))
            job, capability = repository.create_job(
                target_os="arch",
                target_arch="x86_64",
                original_filename="demo.deb",
                upload_key="uploads/demo/source.deb",
                upload_sha256="a" * 64,
                upload_size=123,
            )
            self.assertEqual(repository.get_owned(job.id, capability).id, job.id)
            with self.assertRaises(AccessDeniedError):
                repository.get_owned(job.id, "incorrect")
            repository.set_analysis(
                job.id,
                analysis={"recipe": {"id": "test", "supported": True}},
                blockers=[],
                recipe_id="test",
                report_key="reports/demo/report.json",
                status=JobStatus.READY,
            )
            self.assertEqual(repository.queue_build(job.id).status, JobStatus.BUILD_QUEUED.value)
            self.assertTrue(repository.begin_build(job.id))
            self.assertFalse(repository.begin_build(job.id))
            messages = [entry.message for entry in repository.logs_after(job.id, 0)]
            self.assertIn("Build worker started; checking isolated environment.", messages)
            self.assertNotIn("Isolated build environment started.", messages)

    def test_scoped_download_tokens_and_private_local_storage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_text:
            temporary = Path(temporary_text)
            repository, factory = self._repository(temporary)
            job, _capability = repository.create_job(
                target_os="arch",
                target_arch="x86_64",
                original_filename="demo.deb",
                upload_key="uploads/demo/source.deb",
                upload_sha256="b" * 64,
                upload_size=123,
            )
            repository.complete_build(
                job.id,
                package_key="artifacts/demo/demo.pkg.tar.zst",
                verification={},
                status=JobStatus.SUCCEEDED,
            )
            token, _expires = repository.mint_download_token(job.id, ArtifactKind.PACKAGE)
            token_job, kind = repository.consume_download_token(token)
            self.assertEqual(token_job.id, job.id)
            self.assertEqual(kind, ArtifactKind.PACKAGE)
            with self.assertRaises(AccessDeniedError):
                repository.consume_download_token(token)
            token, _expires = repository.mint_download_token(job.id, ArtifactKind.PACKAGE)
            with factory.begin() as session:
                grant = session.scalar(
                    select(DownloadToken).where(DownloadToken.token_hash == token_hash(token))
                )
                assert grant is not None
                grant.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            with self.assertRaises(AccessDeniedError):
                repository.consume_download_token(token)

            store = LocalArtifactStore(temporary / "private-store")
            store.put_bytes("artifacts/demo/demo.pkg.tar.zst", b"package")
            with store.open("artifacts/demo/demo.pkg.tar.zst") as input_file:
                self.assertEqual(input_file.read(), b"package")
            with self.assertRaises(ArtifactStoreError):
                store.open("../../outside")


if __name__ == "__main__":
    unittest.main()
