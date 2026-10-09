from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

HAS_API_RUNTIME = all(
    find_spec(module) is not None
    for module in ("fastapi", "httpx", "sqlalchemy", "dramatiq", "multipart")
)

if HAS_API_RUNTIME:
    from fastapi.testclient import TestClient

    import app.main as api_main
    from app.config import Settings
    from app.domain import JobStatus
    from app.main import create_app
    from app.storage import ArtifactStoreError

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "flexy-demo_1.0.0_amd64.deb"


@unittest.skipUnless(HAS_API_RUNTIME, "requires FastAPI and SQLAlchemy runtime dependencies")
class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.settings = replace(
            Settings.from_env(),
            database_url=f"sqlite:///{root / 'api.sqlite'}",
            artifact_backend="local",
            artifact_dir=root / "artifacts",
            upload_dir=root / "uploads",
            work_dir=root / "work",
            recipe_dir=ROOT / "backend" / "fixtures" / "recipes",
            queue_mode="inline",
            allow_demo_inline_queue=False,
            auto_create_schema=True,
        )
        self.app = create_app(self.settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.enqueue = patch.object(api_main, "enqueue_analysis")
        self.enqueue.start()

    def tearDown(self) -> None:
        self.enqueue.stop()
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def _upload(self) -> tuple[str, str]:
        response = self.client.post(
            "/v1/jobs",
            data={"target_os": "arch", "target_arch": "x86_64"},
            files={
                "package": (
                    FIXTURE.name,
                    FIXTURE.read_bytes(),
                    "application/vnd.debian.binary-package",
                )
            },
        )
        self.assertEqual(response.status_code, 202, response.text)
        payload = response.json()
        return payload["id"], payload["capability"]

    def test_storage_failure_is_readable_and_does_not_enqueue_analysis(self) -> None:
        with (
            patch.object(
                self.app.state.container.store,
                "put_file",
                side_effect=ArtifactStoreError("private internal details"),
            ),
            patch.object(api_main, "enqueue_analysis") as enqueue,
        ):
            response = self.client.post(
                "/v1/jobs",
                data={"target_os": "arch", "target_arch": "x86_64"},
                files={"package": (FIXTURE.name, FIXTURE.read_bytes())},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "storage_unavailable")
        self.assertNotIn("private internal details", response.text)
        enqueue.assert_not_called()
        self.assertEqual(list(self.settings.upload_dir.glob("incoming-*")), [])

    def test_capability_guards_job_and_scoped_report_download(self) -> None:
        job_id, capability = self._upload()
        self.assertEqual(self.client.get(f"/v1/jobs/{job_id}").status_code, 404)
        job_response = self.client.get(
            f"/v1/jobs/{job_id}", headers={"X-Job-Capability": capability}
        )
        self.assertEqual(job_response.status_code, 200)
        self.assertEqual(job_response.json()["status"], "analyzing")

        container = self.app.state.container
        report_object_key = f"reports/{job_id}/compatibility-report.json"
        container.store.put_bytes(report_object_key, b'{"safe": true}', "application/json")
        container.repository.set_analysis(
            job_id,
            analysis={"recipe": {"id": "test", "supported": True}, "blockers": []},
            blockers=[],
            recipe_id="test",
            report_key=report_object_key,
            status=JobStatus.READY,
        )
        ready = self.client.get(
            f"/v1/jobs/{job_id}", headers={"X-Job-Capability": capability}
        ).json()
        self.assertTrue(ready["analysis"]["recipe"]["supported"])
        self.assertEqual(
            ready["artifacts"],
            [{"kind": "report", "name": "compatibility-report.json", "available": True}],
        )

        grant = self.client.post(
            f"/v1/jobs/{job_id}/downloads/report", headers={"X-Job-Capability": capability}
        )
        self.assertEqual(grant.status_code, 200, grant.text)
        download = self.client.get(grant.json()["url"])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, b'{"safe": true}')
        self.assertEqual(self.client.get(grant.json()["url"]).status_code, 404)

    def test_multipart_body_limit_is_enforced_before_upload_handler(self) -> None:
        self.client.__exit__(None, None, None)
        tiny_settings = replace(self.settings, max_upload_bytes=32)
        self.app = create_app(tiny_settings)
        self.client = TestClient(self.app)
        self.client.__enter__()
        response = self.client.post(
            "/v1/jobs",
            data={"target_os": "arch", "target_arch": "x86_64"},
            files={"package": ("large.deb", b"x" * (300 * 1024), "application/octet-stream")},
        )
        self.assertEqual(response.status_code, 413)

    def test_real_wasi_build_report_and_scoped_package_download(self) -> None:
        from app.services import process_analysis, process_build

        container = self.app.state.container
        container.settings = replace(container.settings, build_executor="wasi")
        job_id, capability = self._upload()
        headers = {"X-Job-Capability": capability}
        process_analysis(job_id, container)
        with patch.object(api_main, "enqueue_build") as enqueue:
            queued = self.client.post(f"/v1/jobs/{job_id}/build", headers=headers)
        self.assertEqual(queued.status_code, 202)
        enqueue.assert_called_once_with(job_id)
        process_build(job_id, container)
        result = self.client.get(f"/v1/jobs/{job_id}", headers=headers).json()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["verification"]["packageCreation"]["state"], "passed")
        for check in ("installation", "launch", "functionality"):
            self.assertEqual(result["verification"][check]["state"], "not_run")
        self.assertEqual(self.client.post(f"/v1/jobs/{job_id}/downloads/artifact").status_code, 404)
        grant = self.client.post(f"/v1/jobs/{job_id}/downloads/artifact", headers=headers)
        self.assertEqual(grant.status_code, 200)
        download = self.client.get(grant.json()["url"])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content[:4], b"\x28\xb5\x2f\xfd")
        self.assertEqual(self.client.get(grant.json()["url"]).status_code, 404)
        report_grant = self.client.post(f"/v1/jobs/{job_id}/downloads/report", headers=headers)
        report = self.client.get(report_grant.json()["url"]).json()
        self.assertEqual(report["verification"], result["verification"])
        self.assertFalse(list(container.settings.work_dir.glob("flexy-wasi-*")))


if __name__ == "__main__":
    unittest.main()
