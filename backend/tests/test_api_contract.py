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


if __name__ == "__main__":
    unittest.main()
