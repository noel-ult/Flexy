from __future__ import annotations

import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.config import Settings
from app.recipes import RecipeRegistry
from app.runners import BubblewrapRunner, BuildEnvironmentUnavailable


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
RECIPES = ROOT / "backend" / "fixtures" / "recipes"


@unittest.skipUnless(
    shutil.which("bwrap") and shutil.which("unshare") and shutil.which("makepkg") and shutil.which("pacman"),
    "requires local Arch build tools and Bubblewrap",
)
class LocalRunnerIntegrationTests(unittest.TestCase):
    def test_demo_recipe_creates_installs_launches_and_self_tests_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            settings = replace(
                Settings.from_env(),
                work_dir=Path(temporary) / "work",
                artifact_dir=Path(temporary) / "artifacts",
                upload_dir=Path(temporary) / "uploads",
                job_timeout_seconds=120,
            )
            recipe = RecipeRegistry.from_directory(RECIPES).get("flexy-demo-1.0.0-amd64-arch-x86_64")
            assert recipe is not None
            runner = BubblewrapRunner(settings)
            result = None
            try:
                result = runner.run(FIXTURES / "flexy-demo_1.0.0_amd64.deb", recipe, lambda _level, _line: None)
            except BuildEnvironmentUnavailable as error:
                self.skipTest(str(error))
            try:
                self.assertIsNotNone(result.package_path)
                self.assertTrue(result.package_path.name.endswith(".pkg.tar.zst"))
                self.assertEqual(result.verification["packageCreation"]["state"], "passed")
                self.assertEqual(result.verification["installation"]["state"], "passed")
                self.assertEqual(result.verification["launch"]["state"], "passed")
                self.assertEqual(result.verification["functionality"]["state"], "passed")
            finally:
                if result is not None:
                    runner.cleanup(result)


if __name__ == "__main__":
    unittest.main()
