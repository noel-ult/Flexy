"""Trusted guest-side command: stdin is the .deb, stdout is bounded JSONL evidence.

Install with the reviewed app/config, inspection, recipes and bwrap modules in an
offline Arch VM. No credentials, URLs or arbitrary command arguments are accepted.
"""

from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path("/home/flexy/runner")
sys.path.insert(0, str(ROOT / "backend"))

from app.config import Settings
from app.inspection import DebInspector, InspectionLimits
from app.recipes import RecipeRegistry
from app.runners import BubblewrapRunner

RECIPE = "flexy-demo-1.0.0-amd64-arch-x86_64"
LIMIT = 1024 * 1024
CHECKS = ("packageCreation", "installation", "launch", "functionality")


def emit(value):
    print(json.dumps(value), flush=True)


def main():
    parent = ROOT / "data" / "remote"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix="job-", dir=parent))
    runner_result = None
    result = {
        "verification": {
            name: {
                "state": "not_run",
                "detail": "Native worker could not complete safely.",
            }
            for name in CHECKS
        },
        "package": None,
    }
    try:
        source = workspace / "source.deb"
        data = sys.stdin.buffer.read(LIMIT + 1)
        if not data or len(data) > LIMIT:
            raise ValueError("Native source size limit exceeded.")
        source.write_bytes(data)
        limits = InspectionLimits(max_expanded_bytes=LIMIT, max_file_count=100)
        registry = RecipeRegistry.from_directory(ROOT / "backend/fixtures/recipes")
        selection = registry.select(
            DebInspector(limits).inspect(source), "arch", "x86_64"
        )
        if (
            selection.recipe is None
            or selection.recipe.identifier != RECIPE
            or selection.blockers
        ):
            raise ValueError("Source does not match the exact reviewed recipe.")
        settings = replace(
            Settings.from_env(),
            work_dir=workspace / "work",
            max_upload_bytes=LIMIT,
            max_extraction_bytes=LIMIT,
            max_file_count=100,
            job_timeout_seconds=30,
            log_limit_bytes=65536,
        )
        runner = BubblewrapRunner(settings, limits)
        runner_result = runner.run(
            source,
            selection.recipe,
            lambda level, message: emit(
                {
                    "log": message[:2000],
                    "level": level,
                }
            ),
        )
        result["verification"] = runner_result.verification
        if runner_result.package_path:
            if runner_result.package_path.stat().st_size > LIMIT:
                raise ValueError("Output exceeds native artifact limit.")
            result["package"] = base64.b64encode(
                runner_result.package_path.read_bytes()
            ).decode()
    except Exception as error:  # noqa: BLE001 - isolated worker reports safe failures
        emit({"log": f"Native guest failed safely: {type(error).__name__}."})
    finally:
        if runner_result is not None:
            BubblewrapRunner.cleanup(runner_result)
        shutil.rmtree(workspace)
    emit({"result": result})


if __name__ == "__main__":
    main()
