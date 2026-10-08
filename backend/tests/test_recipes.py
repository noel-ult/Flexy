from __future__ import annotations

import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from app.inspection import DebInspector
from app.recipes import RecipeError, RecipeRegistry


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"
RECIPES = ROOT / "backend" / "fixtures" / "recipes"


class RecipeSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = RecipeRegistry.from_directory(RECIPES)

    def test_exact_demo_fixture_selects_only_supported_recipe(self) -> None:
        inspection = DebInspector().inspect(FIXTURES / "flexy-demo_1.0.0_amd64.deb")
        selected = self.registry.select(inspection, "arch", "x86_64")
        self.assertTrue(selected.supported)
        self.assertEqual(selected.recipe.identifier, "flexy-demo-1.0.0-amd64-arch-x86_64")
        self.assertEqual(selected.mapped_dependencies[0]["arch"], "glibc")
        self.assertIn('"$startdir/payload/usr/bin/flexy-demo"', selected.recipe.generated_pkgbuild())

    def test_modified_payload_cannot_reuse_demo_recipe(self) -> None:
        inspection = DebInspector().inspect(FIXTURES / "flexy-demo_1.0.0_amd64.deb")
        altered = dataclasses.replace(inspection, data_file_hashes={"usr/bin/flexy-demo": "0" * 64})
        selected = self.registry.select(altered, "arch", "x86_64")
        self.assertFalse(selected.supported)
        self.assertIn("recipe_payload_mismatch", {item["code"] for item in selected.blockers})

    def test_unsupported_fixture_rejects_unmapped_dependency_and_script(self) -> None:
        inspection = DebInspector().inspect(FIXTURES / "unsupported-demo_1.0.0_amd64.deb")
        selected = self.registry.select(inspection, "arch", "x86_64")
        codes = {item["code"] for item in selected.blockers}
        self.assertFalse(selected.supported)
        self.assertIn("unsupported_dependency", codes)
        self.assertIn("unsupported_maintainer_script", codes)

    def test_recipe_paths_cannot_inject_shell_syntax(self) -> None:
        manifest = json.loads((RECIPES / "flexy-demo-1.0.0.json").read_text(encoding="utf-8"))
        digest = manifest["source"]["files"].pop("usr/bin/flexy-demo")
        manifest["source"]["files"]["usr/bin/$(unexpected)"] = digest
        mode = manifest["source"]["fileModes"].pop("usr/bin/flexy-demo")
        manifest["source"]["fileModes"]["usr/bin/$(unexpected)"] = mode
        manifest["verification"]["entrypoint"] = "usr/bin/$(unexpected)"
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unsafe.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(RecipeError):
                RecipeRegistry.from_directory(Path(temporary))


if __name__ == "__main__":
    unittest.main()
