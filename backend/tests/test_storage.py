from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.config import Settings
from app.storage import ArtifactStoreError, LocalArtifactStore, S3ArtifactStore


class LocalStorageTests(unittest.TestCase):
    """Storage must be importable and testable without optional database packages."""

    def test_private_artifact_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = LocalArtifactStore(Path(temporary))
            key = "reports/test-job/report.json"
            store.put_bytes(key, b'{"status":"unverified"}', "application/json")
            with store.open(key) as artifact:
                self.assertEqual(artifact.read(), b'{"status":"unverified"}')
            self.assertTrue(store.exists(key))
            store.delete(key)
            self.assertFalse(store.exists(key))

    def test_path_traversal_and_symlink_escape_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LocalArtifactStore(root / "private")
            outside = root / "outside"
            outside.mkdir()
            (store.root / "escape").symlink_to(outside, target_is_directory=True)
            for key in ("../outside/payload", "/absolute/payload", "escape/payload"):
                with self.subTest(key=key), self.assertRaises(ArtifactStoreError):
                    store.put_bytes(key, b"untrusted")
            self.assertEqual(list(outside.iterdir()), [])


class S3StorageTests(unittest.TestCase):
    def _store(self, encryption: str) -> tuple[S3ArtifactStore, Mock]:
        client = Mock()
        settings = replace(
            Settings.from_env(), s3_bucket="private", s3_server_side_encryption=encryption
        )
        mocked_boto = SimpleNamespace(client=Mock(return_value=client))
        with patch.dict("sys.modules", {"boto3": mocked_boto}):
            store = S3ArtifactStore(settings)
        return store, client

    def test_encryption_is_explicit_for_files_and_streams(self) -> None:
        for encryption in ("AES256", "aws:kms", "none"):
            with self.subTest(encryption=encryption):
                store, client = self._store(encryption)
                expected = {"ContentType": "application/json"}
                if encryption != "none":
                    expected["ServerSideEncryption"] = encryption
                store.put_file("reports/job/report.json", Path("unused.json"), "application/json")
                store.put_stream("reports/job/report.json", BytesIO(b"{}"), "application/json")
                self.assertEqual(client.upload_file.call_args.kwargs["ExtraArgs"], expected)
                self.assertEqual(client.upload_fileobj.call_args.kwargs["ExtraArgs"], expected)

    def test_encrypted_upload_failure_never_retries_without_encryption(self) -> None:
        store, client = self._store("AES256")
        client.upload_file.side_effect = RuntimeError("KMS unavailable")
        with self.assertRaises(ArtifactStoreError):
            store.put_file("reports/job/report.json", Path("unused.json"))
        client.upload_file.assert_called_once()
        self.assertEqual(
            client.upload_file.call_args.kwargs["ExtraArgs"]["ServerSideEncryption"], "AES256"
        )

    def test_invalid_encryption_mode_fails_configuration(self) -> None:
        with self.assertRaises(ArtifactStoreError):
            self._store("typo")
