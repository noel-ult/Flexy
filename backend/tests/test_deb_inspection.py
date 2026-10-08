from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.inspection import DebInspector, InspectionLimits, PackageInspectionError, safe_extract_data

from .helpers import write_deb

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "fixtures"


class DebInspectionTests(unittest.TestCase):
    def test_supported_fixture_reports_elf_and_declared_dependency(self) -> None:
        inspection = DebInspector().inspect(FIXTURES / "flexy-demo_1.0.0_amd64.deb")
        self.assertEqual(inspection.package["name"], "flexy-demo")
        self.assertEqual(inspection.package["architecture"], "amd64")
        self.assertEqual(inspection.dependencies[0]["alternatives"][0]["name"], "libc6")
        self.assertEqual(inspection.executables[0]["kind"], "elf")
        self.assertEqual(inspection.executables[0]["architecture"], "x86_64")
        self.assertEqual(inspection.blockers, [])

    def test_unsupported_fixture_is_never_executed_and_reports_maintainer_script(self) -> None:
        inspection = DebInspector().inspect(FIXTURES / "unsupported-demo_1.0.0_amd64.deb")
        self.assertIn("postinst", inspection.maintainer_scripts)
        self.assertIn(
            "unsupported_maintainer_script", {item["code"] for item in inspection.blockers}
        )

    def test_path_traversal_is_rejected_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = write_deb(
                Path(temporary) / "traversal.deb",
                control_entries=[
                    ("./control", "Package: test\nVersion: 1\nArchitecture: amd64\n", 0o644, None)
                ],
                data_entries=[("../../outside", b"nope", 0o644, None)],
            )
            with self.assertRaises(PackageInspectionError) as raised:
                DebInspector().inspect(package)
            self.assertEqual(raised.exception.code, "archive_path_traversal")

    def test_absolute_symlink_is_rejected_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = write_deb(
                Path(temporary) / "link.deb",
                control_entries=[
                    ("./control", "Package: test\nVersion: 1\nArchitecture: amd64\n", 0o644, None)
                ],
                data_entries=[("./usr/bin/test", b"x", 0o755, "/etc/passwd")],
            )
            with self.assertRaises(PackageInspectionError) as raised:
                DebInspector().inspect(package)
            self.assertEqual(raised.exception.code, "unsafe_archive_link")

    def test_safe_extract_stages_payload_but_never_control_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "script-ran"
            package = write_deb(
                root / "script.deb",
                control_entries=[
                    ("./control", "Package: test\nVersion: 1\nArchitecture: amd64\n", 0o644, None),
                    ("./postinst", f"touch {marker}\n", 0o755, None),
                ],
                data_entries=[("./usr/bin/test", b"payload", 0o755, None)],
            )
            destination = root / "stage"
            extracted = safe_extract_data(package, destination)
            self.assertEqual(
                [item.relative_to(destination).as_posix() for item in extracted], ["usr/bin/test"]
            )
            self.assertEqual((destination / "usr/bin/test").read_bytes(), b"payload")
            self.assertFalse(marker.exists())

    def test_expansion_limit_stops_archive_bomb_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            package = write_deb(
                Path(temporary) / "large.deb",
                control_entries=[
                    ("./control", "Package: test\nVersion: 1\nArchitecture: amd64\n", 0o644, None)
                ],
                data_entries=[("./usr/share/blob", b"x" * 64, 0o644, None)],
            )
            with self.assertRaises(PackageInspectionError) as raised:
                DebInspector(InspectionLimits(max_expanded_bytes=32, max_file_count=10)).inspect(
                    package
                )
            self.assertEqual(raised.exception.code, "archive_expanded_size_exceeded")


if __name__ == "__main__":
    unittest.main()
