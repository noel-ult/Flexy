"""Static, bounded validation of returned native packages. Never extracts or runs them."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import PurePosixPath

import zstandard

from .recipes import Recipe

MAX_NATIVE_BYTES = 1024 * 1024


def validate_native_artifact(data: bytes, recipe: Recipe) -> None:
    if not data or len(data) > MAX_NATIVE_BYTES:
        raise ValueError("Native package exceeds its size limit.")
    with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as stream:
        expanded = stream.read(MAX_NATIVE_BYTES + 1)
    if len(expanded) > MAX_NATIVE_BYTES:
        raise ValueError("Native package expansion limit exceeded.")
    allowed = {".PKGINFO", ".BUILDINFO", ".MTREE", *recipe.files}
    directories = {
        str(parent)
        for name in recipe.files
        for parent in PurePosixPath(name).parents
        if str(parent) != "."
    }
    seen: set[str] = set()
    files: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for member in archive:
            name = member.name.removeprefix("./").rstrip("/")
            if (
                len(seen) >= 100
                or name in seen
                or name.startswith("/")
                or ".." in name.split("/")
                or member.uid != 0
                or member.gid != 0
            ):
                raise ValueError("Unsafe or duplicate native archive member.")
            seen.add(name)
            if member.isdir() and name in directories and member.mode == 0o755:
                continue
            if not member.isfile() or name not in allowed:
                raise ValueError("Unexpected native package file, link or installation script.")
            files.add(name)
            stream = archive.extractfile(member)
            assert stream is not None
            content = stream.read(MAX_NATIVE_BYTES + 1)
            if name in recipe.files:
                if hashlib.sha256(content).hexdigest() != recipe.files[name] or member.mode != int(
                    recipe.file_modes.get(name, "0644"), 8
                ):
                    raise ValueError("Native payload hash or permissions differ from recipe.")
            elif name == ".PKGINFO":
                lines = content.decode("utf-8").splitlines()
                fields = {
                    line.partition("=")[0].strip()
                    for line in lines
                    if line and not line.startswith("#")
                }
                if fields - {
                    "pkgname",
                    "pkgbase",
                    "pkgver",
                    "pkgdesc",
                    "url",
                    "builddate",
                    "packager",
                    "size",
                    "arch",
                    "license",
                    "depend",
                    "xdata",
                }:
                    raise ValueError("Unreviewed native package metadata field.")
                for field, value in {
                    "pkgname": recipe.package,
                    "pkgver": f"{recipe.version}-{recipe.pkgrel}",
                    "arch": recipe.target_architecture,
                }.items():
                    if [line for line in lines if line.startswith(field + " = ")] != [
                        f"{field} = {value}"
                    ]:
                        raise ValueError("Native package metadata differs from recipe.")
                if sorted(line for line in lines if line.startswith("depend = ")) != [
                    "depend = " + item for item in recipe.arch_dependencies
                ]:
                    raise ValueError("Native dependencies differ from recipe.")
            if name not in recipe.files and member.mode != 0o644:
                raise ValueError("Unexpected package metadata permissions.")
        if files != allowed:
            raise ValueError("Native archive is missing required payload/metadata files.")
