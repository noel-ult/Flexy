from __future__ import annotations

import io
import tarfile
from pathlib import Path


def make_tar(
    entries: list[tuple[str, bytes | str, int, str | None]], compression: str = "gz"
) -> bytes:
    """Create a tiny tar for parser tests; entries are name/content/mode/link target."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode=f"w:{compression}") as archive:
        for name, content, mode, link_target in entries:
            info = tarfile.TarInfo(name)
            info.mode = mode
            if link_target is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = link_target
                info.size = 0
                archive.addfile(info)
                continue
            data = content.encode("utf-8") if isinstance(content, str) else content
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def ar_member(name: str, payload: bytes) -> bytes:
    encoded_name = f"{name}/".encode("ascii")
    header = (
        encoded_name.ljust(16, b" ")
        + b"0".ljust(12, b" ")
        + b"0".ljust(6, b" ")
        + b"0".ljust(6, b" ")
        + b"100644".ljust(8, b" ")
        + str(len(payload)).encode("ascii").ljust(10, b" ")
        + b"`\n"
    )
    assert len(header) == 60
    return header + payload + (b"\n" if len(payload) % 2 else b"")


def write_deb(
    path: Path,
    *,
    control_entries: list[tuple[str, bytes | str, int, str | None]],
    data_entries: list[tuple[str, bytes | str, int, str | None]],
) -> Path:
    package = b"!<arch>\n"
    package += ar_member("debian-binary", b"2.0\n")
    package += ar_member("control.tar.gz", make_tar(control_entries))
    package += ar_member("data.tar.gz", make_tar(data_entries))
    path.write_bytes(package)
    return path
