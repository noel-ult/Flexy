"""Safe, non-executing Debian package inspection.

This module intentionally implements only the parts of the Debian archive format
needed to inspect a package.  It never invokes `dpkg`, `ar`, a shell, package
maintainer scripts, or a file type helper.  Both analysis and staging use the same
path/link validation rules.
"""

from __future__ import annotations

import hashlib
import io
import os
import posixpath
import re
import stat
import tarfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


AR_MAGIC = b"!<arch>\n"
AR_HEADER_SIZE = 60
MAINTAINER_SCRIPTS = {
    "preinst",
    "postinst",
    "prerm",
    "postrm",
    "config",
    "triggers",
    "shlibs",
}
MAX_CONTROL_BYTES = 1 * 1024 * 1024
MAX_ELF_ANALYSIS_BYTES = 32 * 1024 * 1024


class PackageInspectionError(ValueError):
    """A package is malformed or violates an archive safety invariant."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message

    def as_blocker(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "severity": "blocking"}


@dataclass(frozen=True, slots=True)
class InspectionLimits:
    max_expanded_bytes: int = 250 * 1024 * 1024
    max_file_count: int = 10_000
    max_elf_analysis_bytes: int = MAX_ELF_ANALYSIS_BYTES


@dataclass(slots=True)
class _Budget:
    limits: InspectionLimits
    expanded_bytes: int = 0
    file_count: int = 0

    def consume(self, size: int) -> None:
        self.file_count += 1
        self.expanded_bytes += size
        if self.file_count > self.limits.max_file_count:
            raise PackageInspectionError(
                "archive_file_count_exceeded",
                f"Archive contains more than {self.limits.max_file_count} entries.",
            )
        if self.expanded_bytes > self.limits.max_expanded_bytes:
            raise PackageInspectionError(
                "archive_expanded_size_exceeded",
                f"Archive expands beyond {self.limits.max_expanded_bytes} bytes.",
            )


@dataclass(slots=True)
class _ArMember:
    name: str
    payload: bytes


@dataclass(slots=True)
class _TarRecord:
    path: str
    kind: str
    mode: int
    size: int
    link_target: str | None = None
    sha256: str | None = None
    executable: dict[str, Any] | None = None


@dataclass(slots=True)
class DebInspection:
    package: dict[str, Any]
    dependencies: list[dict[str, Any]]
    executables: list[dict[str, Any]]
    files: list[dict[str, Any]]
    maintainer_scripts: list[str]
    blockers: list[dict[str, Any]]
    control_sha256: str
    data_file_hashes: dict[str, str]
    expanded_bytes: int
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "dependencies": self.dependencies,
            "executables": self.executables,
            "files": self.files,
            "maintainerScripts": self.maintainer_scripts,
            "blockers": self.blockers,
            "controlSha256": self.control_sha256,
            "dataFileHashes": self.data_file_hashes,
            "archive": {
                "expandedBytes": self.expanded_bytes,
                "fileCount": self.file_count,
                "safeToStage": not any(item["code"].startswith("archive_") for item in self.blockers),
            },
        }


class DebInspector:
    def __init__(self, limits: InspectionLimits | None = None):
        self.limits = limits or InspectionLimits()

    def inspect(self, package_path: Path) -> DebInspection:
        members = _read_ar(package_path)
        names = {member.name: member.payload for member in members}
        debian_binary = names.get("debian-binary")
        if debian_binary is None or not debian_binary.startswith(b"2.0"):
            raise PackageInspectionError("invalid_deb_format", "Missing or invalid debian-binary member.")
        control_member = _find_tar_member(members, "control")
        data_member = _find_tar_member(members, "data")
        if control_member is None or data_member is None:
            raise PackageInspectionError("invalid_deb_format", "A .deb must contain control and data archives.")

        budget = _Budget(self.limits)
        control_records, control_contents = _scan_tar(
            control_member.payload,
            "control",
            budget,
            self.limits,
            collect_control=True,
        )
        control_content = control_contents.get("control")
        if control_content is None:
            raise PackageInspectionError("missing_control_file", "control.tar does not contain a control file.")
        fields = _parse_control_file(control_content)
        for required in ("Package", "Version", "Architecture"):
            if not fields.get(required):
                raise PackageInspectionError("invalid_control_file", f"Required field {required} is missing.")

        data_records, _ = _scan_tar(data_member.payload, "data", budget, self.limits, collect_control=False)
        dependencies = _parse_dependencies(fields.get("Depends", ""))
        maintainer_scripts = sorted(
            record.path.rsplit("/", 1)[-1]
            for record in control_records
            if record.kind == "file" and record.path.rsplit("/", 1)[-1] in MAINTAINER_SCRIPTS
        )
        blockers: list[dict[str, Any]] = []
        if fields["Architecture"] != "amd64":
            blockers.append(
                {
                    "code": "unsupported_package_architecture",
                    "message": f"Debian architecture {fields['Architecture']} is not supported for Arch x86_64.",
                    "severity": "blocking",
                }
            )
        for script in maintainer_scripts:
            blockers.append(
                {
                    "code": "unsupported_maintainer_script",
                    "message": f"Maintainer script '{script}' is present and will not be executed or translated.",
                    "severity": "blocking",
                    "path": script,
                }
            )

        executable_records = [item.executable for item in data_records if item.executable is not None]
        for executable in executable_records:
            assert executable is not None
            if executable["kind"] in {"pe", "macho"}:
                platform = "Windows" if executable["kind"] == "pe" else "macOS"
                blockers.append(
                    {
                        "code": f"{executable['kind']}_binary",
                        "message": f"{executable['path']} is a {platform} binary; changing packages cannot run it on Linux.",
                        "severity": "blocking",
                        "path": executable["path"],
                    }
                )
            elif executable["kind"] == "elf" and executable.get("architecture") != "x86_64":
                blockers.append(
                    {
                        "code": "incompatible_elf_architecture",
                        "message": f"{executable['path']} targets {executable.get('architecture', 'an unknown architecture')}, not x86_64.",
                        "severity": "blocking",
                        "path": executable["path"],
                    }
                )
            elif executable["kind"] == "script":
                blockers.append(
                    {
                        "code": "script_requires_explicit_recipe",
                        "message": f"{executable['path']} is a script and has no automatically translated install/runtime recipe.",
                        "severity": "blocking",
                        "path": executable["path"],
                    }
                )
            elif executable["kind"] == "unknown":
                blockers.append(
                    {
                        "code": "unknown_executable_format",
                        "message": f"{executable['path']} has an unsupported executable format.",
                        "severity": "blocking",
                        "path": executable["path"],
                    }
                )

        files = [
            {
                "path": record.path,
                "kind": record.kind,
                "mode": format(record.mode & 0o777, "04o"),
                "size": record.size,
                **({"sha256": record.sha256} if record.sha256 else {}),
                **({"linkTarget": record.link_target} if record.link_target else {}),
            }
            for record in data_records
        ]
        hashes = {record.path: record.sha256 for record in data_records if record.kind == "file" and record.sha256}
        package = {
            "name": fields["Package"],
            "version": fields["Version"],
            "architecture": fields["Architecture"],
            "description": fields.get("Description", ""),
            "sourceFormat": "deb",
        }
        return DebInspection(
            package=package,
            dependencies=dependencies,
            executables=[item for item in executable_records if item is not None],
            files=files,
            maintainer_scripts=maintainer_scripts,
            blockers=blockers,
            control_sha256=hashlib.sha256(control_content).hexdigest(),
            data_file_hashes=hashes,
            expanded_bytes=budget.expanded_bytes,
            file_count=budget.file_count,
        )


def _read_ar(package_path: Path) -> list[_ArMember]:
    try:
        file_size = package_path.stat().st_size
    except OSError as exc:
        raise PackageInspectionError("package_unavailable", "Uploaded package is unavailable for inspection.") from exc
    if file_size < len(AR_MAGIC):
        raise PackageInspectionError("invalid_deb_format", "File is too small to be a Debian package.")
    members: list[_ArMember] = []
    seen: set[str] = set()
    with package_path.open("rb") as input_file:
        if input_file.read(len(AR_MAGIC)) != AR_MAGIC:
            raise PackageInspectionError("invalid_deb_format", "File is not an ar archive used by .deb packages.")
        while True:
            header = input_file.read(AR_HEADER_SIZE)
            if not header:
                break
            if len(header) != AR_HEADER_SIZE or header[58:60] != b"`\n":
                raise PackageInspectionError("invalid_deb_format", "Archive member header is malformed.")
            raw_name = header[:16].decode("ascii", "strict").strip()
            if raw_name in {"/", "//"} or raw_name.startswith("/"):
                raise PackageInspectionError(
                    "unsupported_ar_member_name", "GNU ar name tables are not accepted in uploaded Debian packages."
                )
            name = raw_name.rstrip("/")
            if not name or "/" in name or "\\" in name:
                raise PackageInspectionError("invalid_deb_format", "Archive member name is unsafe.")
            try:
                size = int(header[48:58].decode("ascii").strip())
            except ValueError as exc:
                raise PackageInspectionError("invalid_deb_format", "Archive member size is malformed.") from exc
            if size < 0 or input_file.tell() + size > file_size:
                raise PackageInspectionError("invalid_deb_format", "Archive member extends beyond file boundary.")
            if name in seen:
                raise PackageInspectionError("invalid_deb_format", f"Duplicate archive member '{name}'.")
            seen.add(name)
            payload = input_file.read(size)
            if len(payload) != size:
                raise PackageInspectionError("invalid_deb_format", "Archive ended while reading a member.")
            if size % 2:
                if input_file.read(1) != b"\n":
                    raise PackageInspectionError("invalid_deb_format", "Archive padding is malformed.")
            members.append(_ArMember(name=name, payload=payload))
    return members


def _find_tar_member(members: list[_ArMember], prefix: str) -> _ArMember | None:
    candidates = [item for item in members if item.name.startswith(f"{prefix}.tar.")]
    if len(candidates) != 1:
        return None
    return candidates[0]


def _normalise_member_path(name: str) -> str:
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        raise PackageInspectionError("archive_path_traversal", f"Unsafe archive path: {name!r}")
    raw = name
    while raw.startswith("./"):
        raw = raw[2:]
    normalized = posixpath.normpath(raw)
    if normalized in {"", "."}:
        return "."
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        raise PackageInspectionError("archive_path_traversal", f"Unsafe archive path: {name!r}")
    return normalized


def _normalise_link_target(path: str, target: str) -> str:
    if not target or "\x00" in target or "\\" in target or target.startswith("/"):
        raise PackageInspectionError("unsafe_archive_link", f"Unsafe link target for {path}: {target!r}")
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        raise PackageInspectionError("unsafe_archive_link", f"Unsafe link target for {path}: {target!r}")
    return normalized


def _scan_tar(
    compressed_tar: bytes,
    label: str,
    budget: _Budget,
    limits: InspectionLimits,
    *,
    collect_control: bool,
) -> tuple[list[_TarRecord], dict[str, bytes]]:
    records: list[_TarRecord] = []
    contents: dict[str, bytes] = {}
    seen_paths: set[str] = set()
    try:
        archive = tarfile.open(fileobj=io.BytesIO(compressed_tar), mode="r:*")
    except (tarfile.TarError, EOFError) as exc:
        raise PackageInspectionError("invalid_tar_archive", f"{label}.tar cannot be safely read.") from exc
    with archive:
        for member in archive:
            path = _normalise_member_path(member.name)
            if path == "." and member.isdir():
                continue
            if path in seen_paths:
                raise PackageInspectionError("duplicate_archive_path", f"{label}.tar contains duplicate path {path}.")
            seen_paths.add(path)
            budget.consume(member.size if member.isfile() else 0)
            if member.isdev() or member.isfifo() or getattr(member, "issparse", lambda: False)():
                raise PackageInspectionError(
                    "unsafe_archive_entry", f"{label}.tar contains a device or FIFO entry at {path}."
                )
            if member.issym() or member.islnk():
                target = _normalise_link_target(path, member.linkname)
                records.append(
                    _TarRecord(
                        path=path,
                        kind="symlink" if member.issym() else "hardlink",
                        mode=member.mode,
                        size=0,
                        link_target=target,
                    )
                )
                continue
            if member.isdir():
                records.append(_TarRecord(path=path, kind="directory", mode=member.mode, size=0))
                continue
            if not member.isfile():
                raise PackageInspectionError("unsafe_archive_entry", f"Unsupported archive entry at {path}.")
            file_object = archive.extractfile(member)
            if file_object is None:
                raise PackageInspectionError("invalid_tar_archive", f"Could not read regular file {path}.")
            sha256, content = _hash_and_sample(file_object, member.size, path, member.mode, limits, collect_control)
            record = _TarRecord(path=path, kind="file", mode=member.mode, size=member.size, sha256=sha256)
            if _is_executable_candidate(path, member.mode):
                record.executable = _classify_executable(path, content, member.size)
            if collect_control and path in {"control", "./control"}:
                if member.size > MAX_CONTROL_BYTES:
                    raise PackageInspectionError("control_file_too_large", "Debian control file exceeds safe parsing limit.")
                # _hash_and_sample retains complete control content when collect_control is set.
                contents[path] = content
            records.append(record)
    return records, contents


def _hash_and_sample(
    source: BinaryIO,
    size: int,
    path: str,
    mode: int,
    limits: InspectionLimits,
    collect_control: bool,
) -> tuple[str, bytes]:
    digest = hashlib.sha256()
    should_retain = (collect_control and path == "control") or (
        _is_executable_candidate(path, mode) and size <= limits.max_elf_analysis_bytes
    )
    retained = bytearray()
    head = bytearray()
    remaining = size
    while remaining:
        chunk = source.read(min(128 * 1024, remaining))
        if not chunk:
            raise PackageInspectionError("invalid_tar_archive", f"Archive ended while reading {path}.")
        remaining -= len(chunk)
        digest.update(chunk)
        if len(head) < 8192:
            head.extend(chunk[: 8192 - len(head)])
        if should_retain:
            retained.extend(chunk)
    # Executables too large for bounded structural inspection still get a format sample.
    return digest.hexdigest(), bytes(retained if should_retain else head)


def _is_executable_candidate(path: str, mode: int) -> bool:
    return bool(mode & 0o111) or path.startswith("usr/bin/") or path.startswith("usr/sbin/")


def _classify_executable(path: str, content: bytes, original_size: int) -> dict[str, Any]:
    result: dict[str, Any] = {"path": path, "kind": "unknown", "size": original_size}
    if content.startswith(b"\x7fELF"):
        result.update(_parse_elf(content))
    elif content.startswith(b"MZ"):
        result.update({"kind": "pe", "architecture": "windows"})
    elif content[:4] in {
        b"\xfe\xed\xfa\xce",
        b"\xfe\xed\xfa\xcf",
        b"\xce\xfa\xed\xfe",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }:
        result.update({"kind": "macho", "architecture": "macos"})
    elif content.startswith(b"#!"):
        first_line = content.split(b"\n", 1)[0].decode("utf-8", "replace")[:256]
        result.update({"kind": "script", "interpreter": first_line[2:].strip()})
    elif original_size > MAX_ELF_ANALYSIS_BYTES:
        result.update({"kind": "unknown", "reason": "executable exceeds bounded structural analysis limit"})
    return result


def _parse_elf(content: bytes) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": "elf", "architecture": "unknown", "dynamicLibraries": []}
    if len(content) < 20:
        result["parseError"] = "truncated ELF header"
        return result
    elf_class = content[4]
    data_encoding = content[5]
    if elf_class not in {1, 2} or data_encoding not in {1, 2}:
        result["parseError"] = "unsupported ELF encoding"
        return result
    order = "little" if data_encoding == 1 else "big"
    machine = int.from_bytes(content[18:20], order)
    result["architecture"] = {3: "x86", 40: "arm", 62: "x86_64", 183: "aarch64"}.get(
        machine, f"elf-machine-{machine}"
    )
    result["bitness"] = 64 if elf_class == 2 else 32
    try:
        if elf_class == 2:
            phoff, phentsize, phnum = (
                int.from_bytes(content[32:40], order),
                int.from_bytes(content[54:56], order),
                int.from_bytes(content[56:58], order),
            )
            dynamic_entry_size = 16
        else:
            phoff, phentsize, phnum = (
                int.from_bytes(content[28:32], order),
                int.from_bytes(content[42:44], order),
                int.from_bytes(content[44:46], order),
            )
            dynamic_entry_size = 8
        if not phentsize or phnum > 512 or phoff + phentsize * phnum > len(content):
            raise ValueError("invalid program headers")
        loads: list[tuple[int, int, int]] = []
        dynamic: tuple[int, int] | None = None
        for index in range(phnum):
            offset = phoff + index * phentsize
            p_type = int.from_bytes(content[offset : offset + 4], order)
            if elf_class == 2:
                p_offset = int.from_bytes(content[offset + 8 : offset + 16], order)
                p_vaddr = int.from_bytes(content[offset + 16 : offset + 24], order)
                p_filesz = int.from_bytes(content[offset + 32 : offset + 40], order)
            else:
                p_offset = int.from_bytes(content[offset + 4 : offset + 8], order)
                p_vaddr = int.from_bytes(content[offset + 8 : offset + 12], order)
                p_filesz = int.from_bytes(content[offset + 16 : offset + 20], order)
            if p_offset + p_filesz > len(content):
                raise ValueError("program segment outside file")
            if p_type == 1:
                loads.append((p_vaddr, p_offset, p_filesz))
            elif p_type == 2:
                dynamic = (p_offset, p_filesz)
            elif p_type == 3:
                interpreter = content[p_offset : p_offset + p_filesz].split(b"\0", 1)[0]
                result["interpreter"] = interpreter.decode("utf-8", "replace")
        if dynamic is not None:
            result["dynamicLibraries"] = _elf_needed_libraries(
                content, dynamic, loads, elf_class, order, dynamic_entry_size
            )
    except ValueError as exc:
        result["parseError"] = str(exc)
    return result


def _elf_needed_libraries(
    content: bytes,
    dynamic: tuple[int, int],
    loads: list[tuple[int, int, int]],
    elf_class: int,
    order: str,
    entry_size: int,
) -> list[str]:
    dynamic_offset, dynamic_size = dynamic
    strtab_address: int | None = None
    strtab_size: int | None = None
    needed_offsets: list[int] = []
    for offset in range(dynamic_offset, dynamic_offset + dynamic_size, entry_size):
        if offset + entry_size > len(content):
            raise ValueError("dynamic table outside file")
        width = 8 if elf_class == 2 else 4
        tag = int.from_bytes(content[offset : offset + width], order, signed=True)
        value = int.from_bytes(content[offset + width : offset + entry_size], order)
        if tag == 0:
            break
        if tag == 1:
            needed_offsets.append(value)
        elif tag == 5:
            strtab_address = value
        elif tag == 10:
            strtab_size = value
    if strtab_address is None:
        return []
    string_offset: int | None = None
    for vaddr, file_offset, size in loads:
        if vaddr <= strtab_address < vaddr + size:
            string_offset = file_offset + strtab_address - vaddr
            break
    if string_offset is None or string_offset >= len(content):
        return []
    max_end = min(len(content), string_offset + (strtab_size or len(content) - string_offset))
    libraries: list[str] = []
    for needed in needed_offsets:
        start = string_offset + needed
        if start >= max_end:
            continue
        end = content.find(b"\0", start, max_end)
        if end < 0:
            continue
        name = content[start:end].decode("utf-8", "replace")
        if name and len(name) <= 255:
            libraries.append(name)
    return libraries


def _parse_control_file(content: bytes) -> dict[str, str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageInspectionError("invalid_control_file", "Debian control metadata is not UTF-8.") from exc
    fields: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and current:
            fields[current] += "\n" + line[1:]
            continue
        if not line.strip():
            break
        if ":" not in line:
            raise PackageInspectionError("invalid_control_file", "Malformed Debian control metadata line.")
        key, value = line.split(":", 1)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", key):
            raise PackageInspectionError("invalid_control_file", "Invalid Debian control field name.")
        fields[key] = value.strip()
        current = key
    return fields


_DEPENDENCY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9+.-]*)(?:\s*\(([^)]+)\))?(?:\s*\[[^]]+\])?$")


def _parse_dependencies(value: str) -> list[dict[str, Any]]:
    if not value.strip():
        return []
    dependencies: list[dict[str, Any]] = []
    for raw_item in value.replace("\n", " ").split(","):
        raw_item = raw_item.strip()
        if not raw_item:
            continue
        alternatives: list[dict[str, str]] = []
        for raw_alternative in raw_item.split("|"):
            candidate = raw_alternative.strip()
            match = _DEPENDENCY_RE.fullmatch(candidate)
            if not match:
                alternatives.append({"name": candidate, "constraint": "unparseable"})
            else:
                alternatives.append({"name": match.group(1), "constraint": (match.group(2) or "").strip()})
        dependencies.append({"raw": raw_item, "alternatives": alternatives})
    return dependencies


def safe_extract_data(package_path: Path, destination: Path, limits: InspectionLimits | None = None) -> list[Path]:
    """Stage only safe payload entries into an empty service-owned directory.

    This is intentionally separate from recipe transformation: an uploaded package
    is never installed and no maintainer script is evaluated.
    """
    limits = limits or InspectionLimits()
    inspection = DebInspector(limits).inspect(package_path)  # Revalidates all archive members.
    if destination.exists() and any(destination.iterdir()):
        raise PackageInspectionError("unsafe_staging_directory", "Destination must be empty before extraction.")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = destination.resolve()
    members = _read_ar(package_path)
    data_member = _find_tar_member(members, "data")
    assert data_member is not None
    extracted: list[Path] = []
    symlinks: list[tuple[str, str]] = []
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data_member.payload), mode="r:*")
    except (tarfile.TarError, EOFError) as exc:  # should be caught by inspect, retained for TOCTOU defense
        raise PackageInspectionError("invalid_tar_archive", "data.tar cannot be safely read.") from exc
    with archive:
        for member in archive:
            path = _normalise_member_path(member.name)
            if path == "." and member.isdir():
                continue
            target = _destination_path(root, path)
            if member.isdir():
                _mkdir_parent_no_links(root, PurePosixPath(path).parent)
                target.mkdir(mode=member.mode & 0o777, exist_ok=True)
                if target.is_symlink():
                    raise PackageInspectionError("unsafe_archive_link", f"Directory {path} is a link.")
                os.chmod(target, member.mode & 0o777)
                continue
            if member.issym():
                symlinks.append((path, _normalise_link_target(path, member.linkname)))
                continue
            if member.islnk():
                # The strict v1 recipe system has no need for hardlinks.  Refusing them
                # avoids aliasing a path in a later recipe through archive ordering.
                raise PackageInspectionError("unsupported_hardlink", f"Hardlink {path} is not supported for staging.")
            if not member.isfile():
                raise PackageInspectionError("unsafe_archive_entry", f"Unsupported archive entry at {path}.")
            _mkdir_parent_no_links(root, PurePosixPath(path).parent)
            if target.exists() or target.is_symlink():
                raise PackageInspectionError("duplicate_archive_path", f"Duplicate archive path {path}.")
            source = archive.extractfile(member)
            if source is None:
                raise PackageInspectionError("invalid_tar_archive", f"Could not read {path}.")
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), member.mode & 0o777)
            try:
                with os.fdopen(fd, "wb") as output:
                    _copy_limited(source, output, member.size)
            except Exception:
                target.unlink(missing_ok=True)
                raise
            extracted.append(target)
    for path, link_target in symlinks:
        target = _destination_path(root, path)
        _mkdir_parent_no_links(root, PurePosixPath(path).parent)
        if target.exists() or target.is_symlink():
            raise PackageInspectionError("duplicate_archive_path", f"Duplicate archive path {path}.")
        # Validated relative to link parent above.  Do not resolve it after creating
        # it; symlink targets are part of the package payload, not filesystem input.
        os.symlink(posixpath.relpath(link_target, posixpath.dirname(path) or "."), target)
        extracted.append(target)
    # Touch the result so static checkers and callers cannot accidentally ignore validation.
    _ = inspection
    return extracted


def _destination_path(root: Path, archive_path: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(archive_path).parts)
    resolved_parent = candidate.parent.resolve()
    if resolved_parent != root and root not in resolved_parent.parents:
        raise PackageInspectionError("archive_path_traversal", f"Archive path escapes staging root: {archive_path}")
    return candidate


def _mkdir_parent_no_links(root: Path, parent: PurePosixPath) -> None:
    current = root
    for part in parent.parts:
        if part in {"", "."}:
            continue
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o755)
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise PackageInspectionError("unsafe_archive_link", f"Staging parent {current.name} is not a directory.")


def _copy_limited(source: BinaryIO, output: BinaryIO, expected: int) -> None:
    remaining = expected
    while remaining:
        chunk = source.read(min(128 * 1024, remaining))
        if not chunk:
            raise PackageInspectionError("invalid_tar_archive", "Archive ended while extracting a file.")
        output.write(chunk)
        remaining -= len(chunk)
