"""Local Bubblewrap executor for disposable, networkless conversion builds."""

from __future__ import annotations

import os
import queue
import resource
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..inspection import InspectionLimits, PackageInspectionError, safe_extract_data
from ..recipes import Recipe


class BuildEnvironmentUnavailable(RuntimeError):
    """The selected isolation mechanism cannot safely run on this host."""


class BuildExecutionError(RuntimeError):
    pass


@dataclass(slots=True)
class CheckResult:
    state: str
    detail: str
    output: str | None = None

    def to_dict(self) -> dict[str, str]:
        result = {"state": self.state, "detail": self.detail}
        if self.output is not None:
            result["output"] = self.output
        return result


@dataclass(slots=True)
class BuildRunResult:
    workspace: Path
    package_path: Path | None
    verification: dict[str, dict[str, str]]


@dataclass(slots=True)
class _CommandResult:
    return_code: int
    output: str
    timed_out: bool


@dataclass(slots=True)
class _LogBudget:
    limit: int
    used: int = 0

    def emit(self, callback: Callable[[str, str], None], level: str, message: str) -> None:
        """Bound database-persisted command logs across the whole job."""
        if self.used >= self.limit:
            return
        encoded = message.encode("utf-8", "replace")
        remaining = self.limit - self.used
        if len(encoded) > remaining:
            # Keep a valid, readable prefix without retaining an unbounded line.
            message = encoded[:remaining].decode("utf-8", "ignore")
            encoded = message.encode("utf-8", "replace")
        if message:
            callback(level, message)
            self.used += len(encoded)


class BubblewrapRunner:
    """Build an allowlisted payload inside a new user/pid/mount/net namespace.

    The host root is never bind-mounted.  A small, read-only runtime subset is
    necessary to access `makepkg`, `pacman`, and their dynamically-linked libraries;
    all mutable paths are service-owned temporary directories under WORK_DIR.
    """

    def __init__(self, settings: Settings, limits: InspectionLimits | None = None):
        self.settings = settings
        self.limits = limits or InspectionLimits(
            max_expanded_bytes=settings.max_extraction_bytes,
            max_file_count=settings.max_file_count,
        )

    def availability(self) -> tuple[bool, str]:
        bwrap = Path(self.settings.bwrap_path)
        if not bwrap.is_file() or not os.access(bwrap, os.X_OK):
            return False, f"Bubblewrap is unavailable at {bwrap}."
        for required in ("makepkg", "pacman"):
            if shutil.which(required) is None:
                return False, f"Required build tool '{required}' is unavailable."
        if shutil.which("unshare") is None:
            return False, "The unshare utility is required for the local user-namespace sandbox."
        return True, "Bubblewrap and local Arch build tools are available."

    def run(
        self,
        package_path: Path,
        recipe: Recipe,
        log: Callable[[str, str], None],
    ) -> BuildRunResult:
        available, reason = self.availability()
        if not available:
            raise BuildEnvironmentUnavailable(reason)
        self.settings.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        workspace = Path(
            __import__("tempfile").mkdtemp(prefix="flexy-build-", dir=self.settings.work_dir)
        ).resolve()
        deadline = time.monotonic() + self.settings.job_timeout_seconds
        log_budget = _LogBudget(self.settings.log_limit_bytes)
        def limited_log(level: str, message: str) -> None:
            log_budget.emit(log, level, message)
        try:
            self._prepare_workspace(workspace, package_path, recipe)
            limited_log("info", "Payload staged without executing package scripts.")
            if time.monotonic() >= deadline:
                verification = {
                    "packageCreation": CheckResult("failed", "Payload staging exceeded the job duration limit.").to_dict(),
                    "installation": CheckResult("not_run", "Package creation did not succeed.").to_dict(),
                    "launch": CheckResult("not_run", "Package creation did not succeed.").to_dict(),
                    "functionality": CheckResult("not_run", "Package creation did not succeed.").to_dict(),
                }
                return BuildRunResult(workspace=workspace, package_path=None, verification=verification)
            build = self._run_command(
                workspace,
                [
                    "makepkg",
                    "--config",
                    "/work/makepkg.conf",
                    "--noconfirm",
                    "--nodeps",
                    "--cleanbuild",
                    "--force",
                ],
                "/work/build",
                limited_log,
                deadline,
            )
            creation = self._check_build_artifact(workspace, build)
            verification: dict[str, dict[str, str]] = {
                "packageCreation": creation.to_dict(),
                "installation": CheckResult("not_run", "Package creation did not succeed.").to_dict(),
                "launch": CheckResult("not_run", "Package creation did not succeed.").to_dict(),
                "functionality": CheckResult("not_run", "Package creation did not succeed.").to_dict(),
            }
            package = self._find_package(workspace) if creation.state == "passed" else None
            if package is None:
                return BuildRunResult(workspace=workspace, package_path=None, verification=verification)

            installation = self._verify_installation(workspace, package, recipe, limited_log, deadline)
            verification["installation"] = installation.to_dict()
            if installation.state != "passed":
                verification["launch"] = CheckResult(
                    "unverified", "Launch verification skipped because installation verification failed."
                ).to_dict()
                verification["functionality"] = CheckResult(
                    "unverified", "Functionality verification skipped because installation verification failed."
                ).to_dict()
                return BuildRunResult(workspace=workspace, package_path=package, verification=verification)

            launch = self._verify_entrypoint(
                workspace,
                recipe,
                recipe.launch_args,
                recipe.expected_launch_output,
                "launch",
                limited_log,
                deadline,
            )
            functionality = self._verify_entrypoint(
                workspace,
                recipe,
                recipe.functionality_args,
                recipe.expected_functionality_output,
                "functionality",
                limited_log,
                deadline,
            )
            verification["launch"] = launch.to_dict()
            verification["functionality"] = functionality.to_dict()
            return BuildRunResult(workspace=workspace, package_path=package, verification=verification)
        except Exception:
            # Caller stores any useful report/log first; its finally block owns cleanup.
            raise

    @staticmethod
    def cleanup(result: BuildRunResult | Path) -> None:
        workspace = result.workspace if isinstance(result, BuildRunResult) else result
        shutil.rmtree(workspace, ignore_errors=True)

    def _prepare_workspace(self, workspace: Path, package_path: Path, recipe: Recipe) -> None:
        for name in (
            "build",
            "out",
            "src",
            "srcpkg",
            "log",
            "cache",
            "pacman-db",
            "verify-root",
            "verify-db",
            "etc",
            "empty-hooks",
        ):
            (workspace / name).mkdir(mode=0o700, exist_ok=True)
        safe_extract_data(package_path, workspace / "build" / "payload", self.limits)
        (workspace / "build" / "PKGBUILD").write_text(recipe.generated_pkgbuild(), encoding="utf-8")
        os.chmod(workspace / "build" / "PKGBUILD", 0o600)
        (workspace / "makepkg.conf").write_text(_makepkg_config(), encoding="utf-8")
        (workspace / "pacman.conf").write_text(_pacman_config(), encoding="utf-8")
        uid, gid = os.getuid(), os.getgid()
        (workspace / "etc" / "passwd").write_text(f"flexy:x:{uid}:{gid}:Flexy worker:/tmp:/bin/sh\n", encoding="utf-8")
        (workspace / "etc" / "group").write_text(f"flexy:x:{gid}:\n", encoding="utf-8")
        (workspace / "etc" / "pacman.conf").write_text(_pacman_config(), encoding="utf-8")

    def _sandbox_command(
        self,
        workspace: Path,
        command: list[str],
        cwd: str,
        *,
        app_root: bool = False,
        run_as_root: bool = False,
    ) -> list[str]:
        runtime_binds: list[str] = []
        for path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(path).exists() or Path(path).is_symlink():
                runtime_binds.extend(["--ro-bind", path, path])
        # Some kernels refuse `bwrap --unshare-all` for an unprivileged worker
        # because bwrap's netlink setup occurs before the user namespace mapping.
        # Entering a one-UID user namespace first is still fail-closed: namespace
        # uid 0 maps only to the non-root host worker UID.  The final bwrap layer
        # drops to a nonzero namespace UID before invoking makepkg, so makepkg's
        # ordinary root guard remains active without special flags.
        command_prefix = [
            "unshare",
            "--user",
            "--map-root-user",
            "--net",
            self.settings.bwrap_path,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
            "--cap-drop",
            "ALL",
            "--clearenv",
            *runtime_binds,
            "--ro-bind",
            str(workspace / "etc"),
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/home",
            "--bind",
            str(workspace),
            "/work",
        ]
        if app_root:
            command_prefix.extend(["--ro-bind", str(workspace / "verify-root"), "/app"])
        if not run_as_root:
            command_prefix.extend(["--unshare-user", "--uid", "1000", "--gid", "1000"])
        command_prefix.extend(
            [
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "PATH",
                "/usr/local/sbin:/usr/local/bin:/usr/bin:/bin",
                "--setenv",
                "LC_ALL",
                "C.UTF-8",
                "--chdir",
                cwd,
                "--",
                *command,
            ]
        )
        return command_prefix

    def _run_command(
        self,
        workspace: Path,
        command: list[str],
        cwd: str,
        log: Callable[[str, str], None],
        deadline: float,
        *,
        app_root: bool = False,
        run_as_root: bool = False,
    ) -> _CommandResult:
        if time.monotonic() >= deadline:
            return _CommandResult(return_code=-1, output="", timed_out=True)
        sandbox = self._sandbox_command(
            workspace, command, cwd, app_root=app_root, run_as_root=run_as_root
        )
        log("info", "Running an isolated, network-disabled verification command.")
        process = subprocess.Popen(
            sandbox,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=self._resource_limits,
        )
        assert process.stdout is not None
        lines: queue.Queue[bytes | None] = queue.Queue(maxsize=64)

        def read_output() -> None:
            try:
                # Do not use iterator/readline: a malicious build can print a
                # newline-free stream and force Python to buffer it indefinitely.
                while chunk := os.read(process.stdout.fileno(), 4096):
                    # Back pressure limits memory if an untrusted build floods stdout.
                    while True:
                        try:
                            lines.put(chunk, timeout=0.25)
                            break
                        except queue.Full:
                            if process.poll() is not None:
                                return
            finally:
                while True:
                    try:
                        lines.put(None, timeout=0.25)
                        break
                    except queue.Full:
                        if process.poll() is not None:
                            continue

        reader = threading.Thread(target=read_output, name="flexy-build-log", daemon=True)
        reader.start()
        collected = bytearray()
        collected_bytes = 0
        output_limit = min(64 * 1024, self.settings.log_limit_bytes)
        finished_output = False
        timed_out = False
        while not finished_output or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 and process.poll() is None:
                timed_out = True
                log("error", f"Command exceeded the {self.settings.job_timeout_seconds} second job deadline.")
                self._terminate_process_group(process)
            try:
                line = lines.get(timeout=min(max(remaining, 0.01), 0.25))
            except queue.Empty:
                continue
            if line is None:
                finished_output = True
                continue
            decoded = line.decode("utf-8", "replace")
            if collected_bytes < output_limit:
                kept = line[: max(0, output_limit - collected_bytes)]
                collected.extend(kept)
                collected_bytes += len(kept)
            # Chunk logging intentionally avoids retaining an unbounded partial
            # line. Splitting still keeps normal tool output easy to read.
            for output_line in decoded.splitlines() or [decoded]:
                if output_line:
                    log("info", output_line[:8_000])
        return_code = process.wait(timeout=5)
        process.stdout.close()
        reader.join(timeout=1)
        output = bytes(collected).decode("utf-8", "replace")
        if timed_out:
            return _CommandResult(return_code=return_code, output=output, timed_out=True)
        if return_code != 0 and _looks_like_namespace_failure(output):
            raise BuildEnvironmentUnavailable("Bubblewrap namespace setup failed on this host.")
        return _CommandResult(return_code=return_code, output=output, timed_out=False)

    def _resource_limits(self) -> None:
        # Applied before exec to the local bwrap process and inherited by its child.
        resource.setrlimit(resource.RLIMIT_CPU, (self.settings.job_timeout_seconds, self.settings.job_timeout_seconds + 1))
        address_limit = 1 * 1024 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (address_limit, address_limit))
        file_limit = self.settings.max_extraction_bytes + self.settings.max_upload_bytes
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def _check_build_artifact(self, workspace: Path, command: _CommandResult) -> CheckResult:
        if command.timed_out:
            return CheckResult("failed", "Package creation exceeded the job duration limit.")
        package = self._find_package(workspace)
        if command.return_code != 0:
            return CheckResult("failed", f"makepkg exited with status {command.return_code}.")
        if package is None or package.stat().st_size == 0:
            return CheckResult("failed", "makepkg exited without producing an Arch package.")
        return CheckResult("passed", f"Created {package.name}.")

    @staticmethod
    def _find_package(workspace: Path) -> Path | None:
        packages = sorted((workspace / "out").glob("*.pkg.tar.zst"))
        return packages[0] if len(packages) == 1 else None

    def _verify_installation(
        self,
        workspace: Path,
        package: Path,
        recipe: Recipe,
        log: Callable[[str, str], None],
        deadline: float,
    ) -> CheckResult:
        result = self._run_command(
            workspace,
            [
                "pacman",
                "--config",
                "/work/pacman.conf",
                "--root",
                "/work/verify-root",
                "--dbpath",
                "/work/verify-db",
                "--cachedir",
                "/work/cache",
                "--noconfirm",
                # `-dd` skips dependency checks in the deliberately empty,
                # networkless verification root (the mapping was static-checked).
                "-dd",
                "--noscriptlet",
                "-U",
                f"/work/out/{package.name}",
            ],
            "/work",
            log,
            deadline,
            run_as_root=True,
        )
        installed_entrypoint = workspace / "verify-root" / recipe.entrypoint
        if result.timed_out:
            return CheckResult("failed", "Installation verification exceeded the job duration limit.")
        if result.return_code != 0:
            return CheckResult("failed", f"pacman installation verification exited with status {result.return_code}.")
        if not installed_entrypoint.is_file():
            return CheckResult("failed", "pacman completed but the recipe entrypoint was not installed.")
        return CheckResult(
            "passed",
            "Package installed into a disposable verification root; dependency resolution was disabled in that empty root.",
        )

    def _verify_entrypoint(
        self,
        workspace: Path,
        recipe: Recipe,
        arguments: tuple[str, ...],
        expected_output: str | None,
        check_name: str,
        log: Callable[[str, str], None],
        deadline: float,
    ) -> CheckResult:
        command = [f"/app/{recipe.entrypoint}", *arguments]
        result = self._run_command(workspace, command, "/work", log, deadline, app_root=True)
        if result.timed_out:
            return CheckResult("failed", f"{check_name.capitalize()} verification exceeded the job duration limit.")
        if result.return_code != 0:
            return CheckResult("failed", f"{check_name.capitalize()} command exited with status {result.return_code}.")
        observed = result.output.strip()
        if expected_output is not None and observed != expected_output:
            return CheckResult(
                "failed",
                f"{check_name.capitalize()} output did not match the recipe expectation.",
                observed[:4_000],
            )
        return CheckResult("passed", f"{check_name.capitalize()} command completed in the disposable root.", observed[:4_000])


def _makepkg_config() -> str:
    return "\n".join(
        [
            'CARCH="x86_64"',
            'CHOST="x86_64-pc-linux-gnu"',
            'PKGDEST="/work/out"',
            'SRCDEST="/work/src"',
            'SRCPKGDEST="/work/srcpkg"',
            'LOGDEST="/work/log"',
            'BUILDDIR="/work/build"',
            "BUILDENV=(!distcc !color !ccache check !sign)",
            "OPTIONS=(strip docs !libtool !staticlibs emptydirs zipman purge !debug lto !autodeps)",
            "INTEGRITY_CHECK=()",
            "PKGEXT='.pkg.tar.zst'",
            "SRCEXT='.src.tar.gz'",
            "COMPRESSZST=(zstd -c -T0 -19 -)",
            "",
        ]
    )


def _pacman_config() -> str:
    return "\n".join(
        [
            "[options]",
            "Architecture = x86_64",
            "SigLevel = Never",
            "LocalFileSigLevel = Never",
            "CacheDir = /work/cache",
            "DBPath = /work/pacman-db",
            "HookDir = /work/empty-hooks",
            "",
        ]
    )


def _looks_like_namespace_failure(output: str) -> bool:
    lowered = output.lower()
    indicators = (
        "creating new namespace",
        "operation not permitted",
        "no permissions to create new namespace",
        "user namespaces are not enabled",
        "bwrap:",
    )
    return any(indicator in lowered for indicator in indicators)
