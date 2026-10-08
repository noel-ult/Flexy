#!/usr/bin/env python3
"""Create the small, redistributable Debian fixtures used by Flexy tests.

The source files are the source of truth. This script deliberately builds only
test data and never invokes a package's maintainer scripts.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
SOURCE = FIXTURES / "source"


def _tar_bytes(files: dict[str, bytes], mode: str = "w:gz") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode=mode, format=tarfile.GNU_FORMAT) as archive:
        for name, content in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            entry.mode = 0o755 if name.endswith("/flexy-demo") or name.endswith("/unsupported-demo") else 0o644
            entry.mtime = 0
            archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


def _ar_member(name: str, content: bytes) -> bytes:
    if len(name) > 15:
        raise ValueError(f"ar member name too long: {name}")
    header = f"{name + '/':<16}{0:<12}{0:<6}{0:<6}{0o100644:<8}{len(content):<10}`\n".encode("ascii")
    return header + content + (b"\n" if len(content) % 2 else b"")


def _deb(output: Path, control: str, data: dict[str, bytes], scripts: dict[str, bytes] | None = None) -> None:
    control_files = {"./control": control.encode("utf-8")}
    if scripts:
        control_files.update({f"./{name}": content for name, content in scripts.items()})
    archive = b"!<arch>\n"
    archive += _ar_member("debian-binary", b"2.0\n")
    archive += _ar_member("control.tar.gz", _tar_bytes(control_files))
    archive += _ar_member("data.tar.gz", _tar_bytes(data))
    output.write_bytes(archive)


def _compile(source: Path, output: Path) -> bytes:
    compiler = shutil.which("cc") or shutil.which("gcc")
    if not compiler:
        raise RuntimeError("A C compiler is required to build the fixture")
    subprocess.run(
        [compiler, "-O2", "-s", "-Wl,--build-id=none", "-o", str(output), str(source)],
        check=True,
    )
    return output.read_bytes()


def build() -> None:
    with tempfile.TemporaryDirectory(prefix="flexy-fixtures-") as temporary:
        work = Path(temporary)
        demo_binary = _compile(SOURCE / "flexy-demo.c", work / "flexy-demo")
        unsupported_binary = _compile(SOURCE / "unsupported-demo.c", work / "unsupported-demo")

        _deb(
            FIXTURES / "flexy-demo_1.0.0_amd64.deb",
            """Package: flexy-demo
Version: 1.0.0
Architecture: amd64
Maintainer: Flexy contributors <fixtures@example.invalid>
Depends: libc6 (>= 2.37)
Description: Tiny supported Flexy conversion fixture
""",
            {
                "./usr/bin/flexy-demo": demo_binary,
                "./usr/share/licenses/flexy-demo/LICENSE": (FIXTURES / "LICENSE").read_bytes(),
            },
        )
        _deb(
            FIXTURES / "unsupported-demo_1.0.0_amd64.deb",
            """Package: unsupported-demo
Version: 1.0.0
Architecture: amd64
Maintainer: Flexy contributors <fixtures@example.invalid>
Depends: libnotmapped (>= 1.0)
Description: Unsupported Flexy safety fixture
""",
            {"./usr/bin/unsupported-demo": unsupported_binary},
            {"postinst": b"#!/bin/sh\necho should-not-run\n"},
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    build()
