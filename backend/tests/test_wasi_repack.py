from __future__ import annotations

import gzip
import hashlib
import io
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.recipes import RecipeRegistry
from app.runners import BubblewrapRunner
from app.runners.wasi import SUPPORTED_RECIPE, WasiRepackRunner, execute_sandbox

HAS_WASI = find_spec("wasmtime") is not None and find_spec("zstandard") is not None
ROOT = Path(__file__).resolve().parents[2]


@unittest.skipUnless(HAS_WASI, "requires the WASI packaging runtime")
class WasiRepackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = replace(Settings.from_env(), work_dir=self.root / "work")
        self.recipe = RecipeRegistry.from_directory(ROOT / "backend/fixtures/recipes").get(
            SUPPORTED_RECIPE
        )
        assert self.recipe is not None
        self.runner = WasiRepackRunner(self.settings)

    def tearDown(self):
        self.temporary.cleanup()

    def test_real_archive_metadata_payload_and_distinct_verification(self):
        import tarfile

        import zstandard

        # No subprocess, shell, uploaded binary or pacman call in this runner.
        with patch("subprocess.Popen", side_effect=AssertionError("No child commands allowed")):
            result = self.runner.run(
                ROOT / "fixtures/flexy-demo_1.0.0_amd64.deb", self.recipe, lambda *_args: None
            )
        assert result.package_path is not None
        unpacked = zstandard.ZstdDecompressor().decompress(
            result.package_path.read_bytes(), max_output_size=2 * 1024 * 1024
        )
        with tarfile.open(fileobj=io.BytesIO(unpacked)) as archive:
            info = archive.extractfile(".PKGINFO").read().decode()
            self.assertIn("pkgver = 1.0.0-1", info)
            self.assertIn("arch = x86_64", info)
            self.assertIn("depend = glibc", info)
            self.assertIn("xdata = pkgtype=pkg", info)
            buildinfo = archive.extractfile(".BUILDINFO").read().decode()
            self.assertIn("buildtool = flexy-wasi-repack", buildinfo)
            self.assertNotIn("installed = glibc", buildinfo)
            mtree = gzip.decompress(archive.extractfile(".MTREE").read()).decode()
            for name, digest in self.recipe.files.items():
                self.assertEqual(
                    hashlib.sha256(archive.extractfile(name).read()).hexdigest(), digest
                )
                self.assertEqual(
                    archive.getmember(name).mode, int(self.recipe.file_modes.get(name, "0644"), 8)
                )
                self.assertIn("sha256digest=" + digest, mtree)
        self.assertEqual(result.verification["packageCreation"]["state"], "passed")
        for name in ("installation", "launch", "functionality"):
            self.assertEqual(result.verification[name]["state"], "not_run")
        BubblewrapRunner.cleanup(result)
        self.assertFalse(result.workspace.exists())

    @unittest.skipUnless(shutil.which("pacman"), "requires pacman for read-only metadata check")
    def test_pacman_reads_actual_arch_package_without_installation(self):
        result = self.runner.run(
            ROOT / "fixtures/flexy-demo_1.0.0_amd64.deb", self.recipe, lambda *_args: None
        )
        read = subprocess.run(
            ["pacman", "-Qip", str(result.package_path)], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(read.returncode, 0, read.stderr)
        self.assertIn("flexy-demo", read.stdout)
        self.assertIn("x86_64", read.stdout)

    def test_unsupported_upload_never_enters_sandbox(self):
        with patch("app.runners.wasi.execute_sandbox") as sandbox:
            with self.assertRaises(ValueError):
                self.runner.run(
                    ROOT / "fixtures/unsupported-demo_1.0.0_amd64.deb",
                    self.recipe,
                    lambda *_args: None,
                )
            sandbox.assert_not_called()

    def test_failed_sandbox_cannot_publish_a_partial_package(self):
        with patch("app.runners.wasi.execute_sandbox", side_effect=TimeoutError):
            result = self.runner.run(
                ROOT / "fixtures/flexy-demo_1.0.0_amd64.deb", self.recipe, lambda *_args: None
            )
        self.assertIsNone(result.package_path)
        self.assertEqual(result.verification["packageCreation"]["state"], "failed")
        for check in ("installation", "launch", "functionality"):
            self.assertEqual(result.verification[check]["state"], "not_run")
        BubblewrapRunner.cleanup(result)

    def _probe_path_denied(self, path: str, write: bool = False):
        source, output = self.root / "input", self.root / "output"
        source.mkdir(exist_ok=True)
        output.mkdir(exist_ok=True)
        (self.root / "secret").write_text("test-only host secret")
        (source / "present").write_text("immutable")
        (source / "escape").symlink_to(self.root / "secret")
        module = f'''(module
          (import "wasi_snapshot_preview1" "path_open"
            (func $open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
          (memory (export "memory") 1)
          (data (i32.const 100) "{path}")
          (func (export "_start")
            (if (i32.eqz (call $open (i32.const 3) (i32.const 0)
              (i32.const 100) (i32.const {len(path)}) (i32.const 0)
              (i64.const {64 if write else 2}) (i64.const 0) (i32.const 0) (i32.const 0)))
              (then unreachable))))'''
        execute_sandbox(module, source, output, 2)
        self.assertEqual((source / "present").read_text(), "immutable")

    def test_no_parent_files_absolute_paths_or_unsafe_links(self):
        for path in ("../secret", "/etc/passwd", "escape"):
            with self.subTest(path=path):
                # Separate inputs for each probe.
                self._probe_path_denied(path)
                (self.root / "input/escape").unlink()

    def test_input_is_read_only(self):
        self._probe_path_denied("present", write=True)

    def test_credentials_are_not_inherited(self):
        source, output = self.root / "input", self.root / "output"
        source.mkdir()
        output.mkdir()
        module = """(module
          (import "wasi_snapshot_preview1" "environ_sizes_get"
            (func $env (param i32 i32) (result i32)))
          (memory (export "memory") 1)
          (func (export "_start")
            (drop (call $env (i32.const 0) (i32.const 4)))
            (if (i32.load (i32.const 0)) (then unreachable))))"""
        with patch.dict("os.environ", {"S3_SECRET_KEY": "test-only-canary"}):
            execute_sandbox(module, source, output, 2)

    def test_cpu_fuel_and_memory_are_bounded(self):
        import wasmtime

        source, output = self.root / "input", self.root / "output"
        source.mkdir()
        output.mkdir()
        infinite = '(module (func (export "_start") (loop $l (br $l))))'
        with self.assertRaises(wasmtime.Trap):
            execute_sandbox(infinite, source, output, 2)
        huge = '(module (memory 3) (func (export "_start")))'
        with self.assertRaises(wasmtime.WasmtimeError):
            execute_sandbox(huge, source, output, 2)
        with self.assertRaises(TimeoutError):
            execute_sandbox(infinite, source, output, 0)


if __name__ == "__main__":
    unittest.main()
