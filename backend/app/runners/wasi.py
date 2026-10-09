"""Data-only recipe repackaging in disposable, capability-restricted WASI stores.

This is not an x86 emulator or a replacement for Arch runtime verification.
No uploaded code, PKGBUILD, or installation script is executed by this runner.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import shutil
import struct
import tarfile
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from ..config import Settings
from ..inspection import DebInspector, InspectionLimits, safe_extract_data
from ..recipes import Recipe, RecipeRegistry
from .bwrap import BuildEnvironmentUnavailable, BuildRunResult, CheckResult

SUPPORTED_RECIPE = "flexy-demo-1.0.0-amd64-arch-x86_64"
MAX_REPACK_BYTES = 1024 * 1024
MODULE_PATH = Path(__file__).with_name("archive.wat")


def execute_sandbox(module_text: str, source: Path, output: Path, timeout: float) -> None:
    """Expose only one read-only input and one job-owned output directory.

    Deliberately do not inherit environment, stdin/stdout, host roots, sockets,
    clocks supplied by the application, or custom Python host callbacks.
    Each invocation owns its engine/store/deadline, including concurrent jobs.
    """
    try:
        import wasmtime
    except ImportError as error:
        raise BuildEnvironmentUnavailable("The WASI packaging runtime is unavailable.") from error
    if timeout <= 0:
        raise TimeoutError("Packaging job duration exceeded.")
    config = wasmtime.Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.wasm_threads = False
    with wasmtime.Engine(config) as engine, wasmtime.Store(engine) as store:
        store.set_limits(memory_size=128 * 1024, memories=1, instances=1, tables=0)
        store.set_fuel(10_000_000)
        store.set_epoch_deadline(1)
        wasi = wasmtime.WasiConfig()
        wasi.env = []
        wasi.argv = []
        wasi.preopen_dir(str(source), "/input", fs_mutable=False)
        wasi.preopen_dir(str(output), "/output", fs_mutable=True)
        store.set_wasi(wasi)
        with wasmtime.Linker(engine) as linker, wasmtime.Module(engine, module_text) as module:
            linker.define_wasi()
            instance = linker.instantiate(store, module)
            timer = threading.Timer(timeout, engine.increment_epoch)
            timer.daemon = True
            timer.start()
            try:
                instance.exports(store)["_start"](store)
            finally:
                timer.cancel()
                timer.join()


class WasiRepackRunner:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(
        self, package_path: Path, recipe: Recipe, log: Callable[[str, str], None]
    ) -> BuildRunResult:
        # This first release intentionally supports just the reviewed demo. Other
        # recipes need an explicit packaging review, not automatic expansion.
        if recipe.identifier != SUPPORTED_RECIPE:
            raise BuildEnvironmentUnavailable("This recipe has no supported WASI repack workflow.")
        limits = InspectionLimits(
            min(self.settings.max_extraction_bytes, MAX_REPACK_BYTES),
            min(self.settings.max_file_count, 100),
        )
        selection = RecipeRegistry([recipe]).select(
            DebInspector(limits).inspect(package_path), "arch", "x86_64"
        )
        if not selection.supported:
            raise ValueError("Payload no longer matches the exact approved recipe.")
        deadline = time.monotonic() + min(self.settings.job_timeout_seconds, 30)
        self.settings.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="flexy-wasi-", dir=self.settings.work_dir))
        try:
            source, output = work / "input", work / "output"
            source.mkdir(mode=0o700)
            output.mkdir(mode=0o700)
            safe_extract_data(package_path, source / "payload", limits)
            _prepare_plan(source, recipe)
            log(
                "info",
                "Creating an Arch archive in a disposable WASI packaging sandbox; "
                "no uploaded code is executed.",
            )
            execute_sandbox(MODULE_PATH.read_text(), source, output, deadline - time.monotonic())
            archive = output / "package.tar"
            _verify_archive(archive, source, recipe)
            import zstandard

            package = (
                output / f"{recipe.package}-{recipe.version}-{recipe.pkgrel}-x86_64.pkg.tar.zst"
            )
            with archive.open("rb") as incoming, package.open("xb") as destination:
                zstandard.ZstdCompressor(level=3, threads=0).copy_stream(incoming, destination)
            if time.monotonic() > deadline:
                raise TimeoutError("Packaging job duration exceeded.")
            # Check the real downloadable bytes, not only the uncompressed input.
            with package.open("rb") as incoming:
                unpacked = (
                    zstandard.ZstdDecompressor()
                    .stream_reader(incoming)
                    .read(MAX_REPACK_BYTES + 128 * 1024)
                )
            if unpacked != archive.read_bytes():
                raise ValueError("Compressed package verification failed.")
            log(
                "info",
                "Package created; metadata, payload hashes, permissions and "
                "compressed archive round-trip verified.",
            )
            verification = {
                "packageCreation": CheckResult(
                    "passed",
                    "Created an unsigned Arch package; "
                    "verified metadata and exact recipe payload. No uploaded code was run.",
                ).to_dict()
            }
            for check in ("installation", "launch", "functionality"):
                verification[check] = CheckResult(
                    "not_run",
                    "Data-only WASI repackaging "
                    "cannot install or execute Linux applications. Requires a separately "
                    "validated x86_64 Arch verification environment.",
                ).to_dict()
            return BuildRunResult(work, package, verification)
        except BuildEnvironmentUnavailable:
            shutil.rmtree(work, ignore_errors=True)
            raise
        except Exception as error:
            # A packaging attempt is a failed creation check, not an unrun check.
            # Do not publish even a partially written output after a trap/deadline.
            log("error", f"WASI package creation failed safely: {type(error).__name__}.")
            checks = {
                "packageCreation": CheckResult(
                    "failed", f"Package creation failed: {type(error).__name__}."
                ).to_dict()
            }
            for check in ("installation", "launch", "functionality"):
                checks[check] = CheckResult(
                    "not_run", "Package creation did not succeed."
                ).to_dict()
            return BuildRunResult(work, None, checks)


def _text(value: str) -> str:
    if "\n" in value or "\r" in value or "\x00" in value:
        raise ValueError("Invalid recipe metadata.")
    return value


def _prepare_plan(source: Path, recipe: Recipe) -> None:
    timestamp = int(time.time())
    version = _text(f"{recipe.version}-{recipe.pkgrel}")
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", version):
        raise ValueError("Unsupported package version for repackaging.")
    size = sum((source / "payload" / name).stat().st_size for name in recipe.files)
    pkginfo = "\n".join(
        [
            f"pkgname = {recipe.package}",
            f"pkgbase = {recipe.package}",
            f"pkgver = {version}",
            f"pkgdesc = {_text(recipe.description)}",
            "url =",
            f"builddate = {timestamp}",
            "packager = Flexy recipe repackager",
            f"size = {size}",
            "arch = x86_64",
            f"license = {_text(recipe.license)}",
            "xdata = pkgtype=pkg",
            *[f"depend = {_text(dep)}" for dep in recipe.arch_dependencies],
            "",
        ]
    )
    buildinfo = "\n".join(
        [
            "format = 2",
            f"pkgname = {recipe.package}",
            f"pkgbase = {recipe.package}",
            f"pkgver = {version}",
            "pkgarch = x86_64",
            "pkgbuild_sha256sum = "
            + hashlib.sha256(recipe.generated_pkgbuild().encode()).hexdigest(),
            "packager = Flexy recipe repackager",
            f"builddate = {timestamp}",
            "builddir = /output",
            "startdir = /input",
            "buildtool = flexy-wasi-repack",
            "buildtoolver = 1.0.0-1-any",
            "options = !strip",
            "",
        ]
    )
    (source / ".PKGINFO").write_text(pkginfo)
    (source / ".BUILDINFO").write_text(buildinfo)
    entries: list[tuple[str, str, int]] = [
        (".PKGINFO", ".PKGINFO", 0o644),
        (".BUILDINFO", ".BUILDINFO", 0o644),
    ]
    directories = {
        str(parent)
        for name in recipe.files
        for parent in PurePosixPath(name).parents
        if str(parent) != "."
    }
    entries += [(name + "/", "", 0o755) for name in sorted(directories)]
    entries += [
        (name, "payload/" + name, int(recipe.file_modes.get(name, "0644"), 8))
        for name in sorted(recipe.files)
    ]
    mtree = ["#mtree", "/set uid=0 gid=0", ". type=dir mode=755"]
    for name, path, mode in entries:
        if path:
            data = (source / path).read_bytes()
            mtree.append(
                f"./{name} type=file mode={mode:o} size={len(data)} "
                f"time={timestamp} sha256digest={hashlib.sha256(data).hexdigest()}"
            )
        else:
            mtree.append(f"./{name.rstrip('/')} type=dir mode={mode:o} time={timestamp}")
    (source / ".MTREE").write_bytes(gzip.compress(("\n".join(mtree) + "\n").encode(), mtime=0))
    entries.insert(2, (".MTREE", ".MTREE", 0o644))
    with (source / "plan").open("xb") as plan:
        plan.write(struct.pack("<I", len(entries)))
        for name, path, mode in entries:
            item = tarfile.TarInfo(name)
            item.mode, item.mtime = mode, timestamp
            item.type = tarfile.REGTYPE if path else tarfile.DIRTYPE
            item.size = (source / path).stat().st_size if path else 0
            encoded = path.encode()
            plan.write(item.tobuf(format=tarfile.USTAR_FORMAT))
            plan.write(struct.pack("<II", len(encoded), item.size))
            plan.write(encoded)


def _verify_archive(archive: Path, source: Path, recipe: Recipe) -> None:
    if archive.stat().st_size > MAX_REPACK_BYTES + 128 * 1024:
        raise ValueError("Package output exceeds its size limit.")
    expected = {".PKGINFO", ".BUILDINFO", ".MTREE", *recipe.files}
    with tarfile.open(fileobj=io.BytesIO(archive.read_bytes()), mode="r:") as package:
        members = package.getmembers()
        directories = {
            str(parent)
            for name in recipe.files
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        }
        if len({item.name for item in members}) != len(members):
            raise ValueError("Duplicate package members.")
        regular = [item for item in members if item.isfile()]
        if {item.name for item in regular} != expected or len(regular) != len(expected):
            raise ValueError("Package file list does not match the recipe.")
        for item in members:
            if not (item.isdir() or item.isfile()) or item.uid != 0 or item.gid != 0:
                raise ValueError("Unsafe package member.")
            if item.isdir() and (item.name.rstrip("/") not in directories or item.mode != 0o755):
                raise ValueError("Unexpected package directory.")
            if item.isfile():
                stream = package.extractfile(item)
                assert stream is not None
                data = stream.read()
                if item.name in recipe.files:
                    if hashlib.sha256(data).hexdigest() != recipe.files[item.name]:
                        raise ValueError("Package payload hash does not match the recipe.")
                path = source / ("payload/" + item.name if item.name in recipe.files else item.name)
                if data != path.read_bytes():
                    raise ValueError("Package payload differs from the approved source.")
                mode = int(recipe.file_modes.get(item.name, "0644"), 8)
                if item.mode != mode:
                    raise ValueError("Package file permissions differ from the recipe.")
