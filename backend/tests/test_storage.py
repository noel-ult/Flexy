from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.storage import ArtifactStoreError, LocalArtifactStore


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
