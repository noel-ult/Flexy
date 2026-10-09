"""Application service composition and worker-side business operations."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .db import create_session_factory
from .domain import JobStatus
from .inspection import DebInspector, InspectionLimits, PackageInspectionError
from .recipes import RecipeRegistry
from .repository import JobRepository, NotFoundError
from .runners import BubblewrapRunner, BuildEnvironmentUnavailable
from .storage import ArtifactStore, create_artifact_store


@dataclass(slots=True)
class ServiceContainer:
    settings: Settings
    repository: JobRepository
    store: ArtifactStore
    recipes: RecipeRegistry
    inspector: DebInspector
    bwrap_runner: BubblewrapRunner

    @classmethod
    def create(cls, settings: Settings | None = None) -> ServiceContainer:
        settings = settings or get_settings()
        sessions = create_session_factory(settings)
        limits = InspectionLimits(
            max_expanded_bytes=settings.max_extraction_bytes,
            max_file_count=settings.max_file_count,
        )
        return cls(
            settings=settings,
            repository=JobRepository(sessions, settings),
            store=create_artifact_store(settings),
            recipes=RecipeRegistry.from_directory(settings.recipe_dir),
            inspector=DebInspector(limits),
            bwrap_runner=BubblewrapRunner(settings, limits),
        )


_container: ServiceContainer | None = None


def get_container() -> ServiceContainer:
    global _container
    if _container is None:
        _container = ServiceContainer.create()
    return _container


def reset_container_for_tests() -> None:
    global _container
    _container = None


def upload_key(job_id: str) -> str:
    return f"uploads/{job_id}/source.deb"


def report_key(job_id: str) -> str:
    return f"reports/{job_id}/compatibility-report.json"


def package_key(job_id: str, name: str) -> str:
    safe_name = "".join(char for char in name if char.isalnum() or char in ".-_+")
    return f"artifacts/{job_id}/{safe_name or 'converted.pkg.tar.zst'}"


def _temporary_upload(
    container: ServiceContainer, job_id: str, upload_object_key: str
) -> tuple[Path, Path]:
    container.settings.work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    work = Path(
        tempfile.mkdtemp(prefix=f"flexy-job-{job_id[:8]}-", dir=container.settings.work_dir)
    )
    source = container.store.materialize(upload_object_key, work / "source.deb")
    return work, source


def _compatibility_report(
    job_id: str,
    analysis: dict[str, Any],
    blockers: list[dict[str, Any]],
    recipe_id: str | None,
    mapped_dependencies: list[dict[str, str]] | None = None,
    verification: dict[str, Any] | None = None,
    error: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "jobId": job_id,
        "generatedAt": datetime.now(UTC).isoformat(),
        "compatibility": {
            "supported": not blockers and recipe_id is not None,
            "recipeId": recipe_id,
            "blockers": blockers,
            "dependencyMappings": mapped_dependencies or [],
            "limitations": [
                "A package-format conversion does not make Windows or macOS binaries run on Linux.",
                "Package creation alone is not desktop compatibility or functionality proof.",
                "The browser only controls the remote conversion service; "
                "any downloaded package runs on your computer.",
            ],
        },
        "analysis": analysis,
        "verification": verification
        or {
            "packageCreation": {"state": "not_run"},
            "installation": {"state": "not_run"},
            "launch": {"state": "not_run"},
            "functionality": {"state": "not_run"},
        },
        **({"error": error} if error else {}),
    }


def process_analysis(job_id: str, container: ServiceContainer | None = None) -> None:
    container = container or get_container()
    workspace: Path | None = None
    try:
        job = container.repository.get(job_id)
        if job.status != JobStatus.ANALYZING.value:
            return
        container.repository.append_log(
            job_id, "info", "Inspecting Debian metadata and payload without executing it."
        )
        workspace, source = _temporary_upload(container, job_id, job.upload_key)
        inspection = container.inspector.inspect(source)
        selection = container.recipes.select(inspection, job.target_os, job.target_arch)
        analysis = inspection.to_dict()
        mapping_by_debian = {item["debian"]: item for item in selection.mapped_dependencies}
        for dependency in analysis["dependencies"]:
            mapped = next(
                (
                    mapping_by_debian.get(alternative.get("name"))
                    for alternative in dependency.get("alternatives", [])
                    if mapping_by_debian.get(alternative.get("name"))
                ),
                None,
            )
            if mapped:
                dependency.update(
                    {
                        "arch": mapped["arch"],
                        "status": "supported",
                        "constraint": mapped["constraint"],
                    }
                )
            else:
                dependency["status"] = "unsupported"
        for executable in analysis["executables"]:
            executable["compatible"] = (
                executable.get("kind") == "elf" and executable.get("architecture") == "x86_64"
            )
        analysis["recipe"] = {
            "id": selection.recipe.identifier if selection.recipe else None,
            "supported": selection.supported,
            "detail": (
                "Exact recipe and explicit dependency mappings match this upload."
                if selection.supported
                else "No exact supported conversion recipe matches this upload."
            ),
            "mappedDependencies": list(selection.mapped_dependencies),
        }
        analysis["limits"] = {
            "uploadBytes": container.settings.max_upload_bytes,
            "extractedBytes": container.settings.max_extraction_bytes,
            "fileCount": container.settings.max_file_count,
        }
        blockers = list(selection.blockers)
        analysis["blockers"] = blockers
        analysis["findings"] = []
        status = JobStatus.READY if selection.supported else JobStatus.UNSUPPORTED
        report = _compatibility_report(
            job_id,
            analysis,
            blockers,
            selection.recipe.identifier if selection.recipe else None,
            list(selection.mapped_dependencies),
        )
        destination = report_key(job_id)
        container.store.put_bytes(
            destination,
            json.dumps(report, indent=2, sort_keys=True).encode("utf-8"),
            "application/json",
        )
        container.repository.set_analysis(
            job_id,
            analysis=analysis,
            blockers=blockers,
            recipe_id=selection.recipe.identifier if selection.recipe else None,
            report_key=destination,
            status=status,
        )
        if status == JobStatus.READY:
            container.repository.append_log(
                job_id, "info", "Exact supported recipe found; build can be started."
            )
        else:
            container.repository.append_log(
                job_id, "warning", "No safe supported conversion recipe is available."
            )
    except PackageInspectionError as error:
        blocker = error.as_blocker()
        analysis = {"sourceFormat": "deb", "safeToAnalyze": False}
        destination = report_key(job_id)
        report = _compatibility_report(job_id, analysis, [blocker], None, error=blocker)
        try:
            container.store.put_bytes(
                destination, json.dumps(report, indent=2).encode("utf-8"), "application/json"
            )
            container.repository.set_analysis(
                job_id,
                analysis=analysis,
                blockers=[blocker],
                recipe_id=None,
                report_key=destination,
                status=JobStatus.UNSUPPORTED,
            )
        except Exception:
            container.repository.mark_error(job_id, code=error.code, message=error.message)
    except NotFoundError:
        return
    except Exception as error:
        container.repository.mark_error(
            job_id,
            code="analysis_failed",
            message=f"Analysis could not complete safely: {type(error).__name__}.",
        )
    finally:
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)


def process_build(job_id: str, container: ServiceContainer | None = None) -> None:
    container = container or get_container()
    workspace: Path | None = None
    build_result = None
    try:
        if not container.repository.begin_build(job_id):
            return
        job = container.repository.get(job_id)
        recipe = container.recipes.get(job.recipe_id)
        if recipe is None:
            container.repository.complete_build(
                job_id,
                package_key=None,
                verification=_not_run_verification("Recipe is unavailable."),
                status=JobStatus.FAILED,
                error_code="recipe_unavailable",
                error_message="The approved recipe is no longer available.",
            )
            return
        workspace, source = _temporary_upload(container, job_id, job.upload_key)
        # Reinspect and reselect before the expensive build: persisted readiness is
        # not treated as authority to build a modified or corrupted object.
        selection = container.recipes.select(
            container.inspector.inspect(source), job.target_os, job.target_arch
        )
        if selection.recipe is None or selection.recipe.identifier != recipe.identifier:
            container.repository.complete_build(
                job_id,
                package_key=None,
                verification=_not_run_verification(
                    "Payload no longer matches the approved recipe."
                ),
                status=JobStatus.FAILED,
                error_code="recipe_revalidation_failed",
                error_message="The uploaded package no longer matches the approved recipe.",
            )
            return
        container.repository.append_log(
            job_id, "info", "Checking availability of the network-isolated build executor."
        )
        if container.settings.build_executor == "bwrap":
            build_result = container.bwrap_runner.run(
                source,
                recipe,
                lambda level, message: container.repository.append_log(job_id, level, message),
            )
        elif container.settings.build_executor == "kubernetes":
            # The controller contract intentionally fails closed until deployment
            # supplies scoped input/output handoff rather than borrowing host access.
            raise BuildEnvironmentUnavailable(
                "Kubernetes execution is not wired with a scoped Job controller "
                "and artifact callback in this deployment."
            )
        else:
            raise BuildEnvironmentUnavailable("No supported isolated build executor is configured.")

        artifact_object_key: str | None = None
        if build_result.package_path is not None:
            artifact_object_key = package_key(job_id, build_result.package_path.name)
            container.store.put_file(
                artifact_object_key, build_result.package_path, "application/zstd"
            )
        status = JobStatus.SUCCEEDED if artifact_object_key else JobStatus.FAILED
        error_message = None if artifact_object_key else "No Arch package was created."
        container.repository.complete_build(
            job_id,
            package_key=artifact_object_key,
            verification=build_result.verification,
            status=status,
            error_code=None if artifact_object_key else "package_creation_failed",
            error_message=error_message,
        )
        _update_report_after_build(container, job_id, build_result.verification)
    except BuildEnvironmentUnavailable as error:
        verification = _not_run_verification(str(error))
        container.repository.complete_build(
            job_id,
            package_key=None,
            verification=verification,
            status=JobStatus.ENVIRONMENT_UNAVAILABLE,
            error_code="build_environment_unavailable",
            error_message=str(error),
        )
        _update_report_after_build(
            container,
            job_id,
            verification,
            error={"code": "build_environment_unavailable", "message": str(error)},
        )
    except PackageInspectionError as error:
        verification = _not_run_verification("Safe restaging failed.")
        container.repository.complete_build(
            job_id,
            package_key=None,
            verification=verification,
            status=JobStatus.FAILED,
            error_code=error.code,
            error_message=error.message,
        )
    except NotFoundError:
        return
    except Exception as error:
        verification = _not_run_verification("Unexpected worker failure.")
        try:
            container.repository.complete_build(
                job_id,
                package_key=None,
                verification=verification,
                status=JobStatus.FAILED,
                error_code="build_failed",
                error_message=f"Build could not complete safely: {type(error).__name__}.",
            )
            _update_report_after_build(container, job_id, verification)
        except Exception:
            pass
    finally:
        if build_result is not None:
            container.bwrap_runner.cleanup(build_result)
        if workspace is not None:
            shutil.rmtree(workspace, ignore_errors=True)


def _not_run_verification(reason: str) -> dict[str, dict[str, str]]:
    return {
        "packageCreation": {"state": "not_run", "detail": reason},
        "installation": {"state": "not_run", "detail": reason},
        "launch": {"state": "not_run", "detail": reason},
        "functionality": {"state": "not_run", "detail": reason},
    }


def _update_report_after_build(
    container: ServiceContainer,
    job_id: str,
    verification: dict[str, Any],
    error: dict[str, str] | None = None,
) -> None:
    try:
        job = container.repository.get(job_id)
        report = _compatibility_report(
            job_id,
            job.analysis_json or {},
            job.blockers_json or [],
            job.recipe_id,
            (job.analysis_json or {}).get("recipe", {}).get("mappedDependencies", []),
            verification,
            error,
        )
        if job.report_key:
            container.store.put_bytes(
                job.report_key,
                json.dumps(report, indent=2, sort_keys=True).encode("utf-8"),
                "application/json",
            )
    except Exception:
        # Build result persistence must not be hidden by a report-rendering failure.
        return


def cleanup_expired_jobs(container: ServiceContainer | None = None) -> int:
    """Delete all service-owned objects before deleting their database records."""
    container = container or get_container()
    removed: list[str] = []
    for job in container.repository.expired_jobs():
        try:
            for prefix in (f"uploads/{job.id}", f"artifacts/{job.id}", f"reports/{job.id}"):
                container.store.delete_prefix(prefix)
        except Exception:
            # Leave metadata intact so the next cleanup run can retry instead of
            # orphaning a private artifact.
            continue
        removed.append(job.id)
    return container.repository.delete_jobs(removed)
