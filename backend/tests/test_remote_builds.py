from __future__ import annotations

import base64
import io
import tarfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import zstandard

import app.main as api_main
from app.models import Job, RemoteWorker
from app.native_artifact import validate_native_artifact
from app.remote_builds import SUPPORTED_RECIPE
from app.services import process_analysis, process_build
from tests import test_api_contract


class RemoteBuildTests(unittest.TestCase):
    _upload = test_api_contract.ApiContractTests._upload
    tearDown = test_api_contract.ApiContractTests.tearDown

    def setUp(self):
        from dataclasses import replace

        test_api_contract.ApiContractTests.setUp(self)
        container = self.app.state.container
        container.settings = replace(
            container.settings, build_executor="remote", remote_build_token="t" * 43
        )
        self.auth = {"Authorization": "Bearer " + "t" * 43}

    def ready(self):
        job_id, capability = self._upload()
        process_analysis(job_id, self.app.state.container)
        return job_id, {"X-Job-Capability": capability}

    def claim(self):
        job_id, owner = self.ready()
        self.assertEqual(
            self.client.post("/v1/native-worker/poll", headers=self.auth).status_code, 200
        )
        with patch.object(api_main, "enqueue_build") as actor:
            self.assertEqual(
                self.client.post(f"/v1/jobs/{job_id}/build", headers=owner).status_code, 202
            )
            actor.assert_not_called()
        claim = self.client.post("/v1/native-worker/poll", headers=self.auth).json()["job"]
        self.assertEqual(claim["id"], job_id)
        return job_id, owner, {**self.auth, "X-Build-Lease": claim["lease"]}

    def failed_result(self):
        return {
            "package": None,
            "verification": {
                name: {"state": "not_run", "detail": "Explicit failed test worker result."}
                for name in ("packageCreation", "installation", "launch", "functionality")
            },
        }

    def test_worker_auth_and_offline_queue(self):
        self.assertEqual(self.client.post("/v1/native-worker/poll").status_code, 401)
        job_id, owner = self.ready()
        result = self.client.post(f"/v1/jobs/{job_id}/build", headers=owner)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(
            self.client.get(f"/v1/jobs/{job_id}", headers=owner).json()["status"], "ready"
        )

    def test_scoped_source_single_claim_and_no_actor_execution(self):
        from tests.test_api_contract import FIXTURE

        job_id, _, auth = self.claim()
        self.assertEqual(
            self.client.get(f"/v1/native-worker/{job_id}/source", headers=auth).content,
            FIXTURE.read_bytes(),
        )
        self.assertEqual(
            self.client.get(f"/v1/native-worker/{job_id}/source", headers=self.auth).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/v1/native-worker/poll", headers=self.auth).json(), {"job": None}
        )
        process_build(job_id, self.app.state.container)
        self.assertEqual(self.app.state.container.repository.get(job_id).status, "building")

    def test_expired_lease_and_replay_fail_closed(self):
        job_id, owner, auth = self.claim()
        with self.app.state.container.repository.sessions.begin() as session:
            session.get(RemoteWorker, 1).lease_expires = datetime.now(UTC) - timedelta(seconds=1)
        self.assertEqual(
            self.client.post(
                f"/v1/native-worker/{job_id}/complete", headers=auth, json=self.failed_result()
            ).status_code,
            404,
        )
        result = self.client.get(f"/v1/jobs/{job_id}", headers=owner).json()
        self.assertEqual(result["status"], "environment_unavailable")
        self.assertTrue(
            all(check["state"] == "not_run" for check in result["verification"].values())
        )

    def test_failure_completion_and_replay(self):
        job_id, owner, auth = self.claim()
        path = f"/v1/native-worker/{job_id}/complete"
        self.assertEqual(
            self.client.post(path, headers=auth, json=self.failed_result()).status_code, 200
        )
        self.assertEqual(
            self.client.post(path, headers=auth, json=self.failed_result()).status_code, 404
        )
        self.assertEqual(
            self.client.get(f"/v1/jobs/{job_id}", headers=owner).json()["status"], "failed"
        )

    def test_bounded_bodies_logs_and_inconsistent_evidence(self):
        job_id, _, auth = self.claim()
        path = f"/v1/native-worker/{job_id}"
        self.assertEqual(
            self.client.post(path + "/log", headers=auth, json={"message": "x" * 4097}).status_code,
            422,
        )
        self.assertEqual(
            self.client.post(
                path + "/complete", headers=auth, content=b"x" * (2 * 1024 * 1024 + 1)
            ).status_code,
            413,
        )
        value = self.failed_result()
        value["verification"]["launch"]["state"] = "passed"
        self.assertEqual(
            self.client.post(path + "/complete", headers=auth, json=value).status_code, 422
        )

    def test_queue_budget_and_simultaneous_claims(self):
        self.client.post("/v1/native-worker/poll", headers=self.auth)
        for _ in range(3):
            job_id, owner = self.ready()
            self.assertEqual(
                self.client.post(f"/v1/jobs/{job_id}/build", headers=owner).status_code, 202
            )
        job_id, owner = self.ready()
        self.assertEqual(
            self.client.post(f"/v1/jobs/{job_id}/build", headers=owner).status_code, 429
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(
                    lambda _: self.client.post("/v1/native-worker/poll", headers=self.auth).json(),
                    range(2),
                )
            )
        self.assertEqual(sum(response["job"] is not None for response in responses), 1)

    def test_queued_jobs_timeout_without_worker(self):
        job_id, owner = self.ready()
        self.client.post("/v1/native-worker/poll", headers=self.auth)
        self.client.post(f"/v1/jobs/{job_id}/build", headers=owner)
        with self.app.state.container.repository.sessions.begin() as session:
            session.get(Job, job_id).build_started_at = datetime.now(UTC) - timedelta(seconds=121)
        self.assertEqual(
            self.client.get(f"/v1/jobs/{job_id}", headers=owner).json()["status"],
            "environment_unavailable",
        )

    def test_real_data_package_completion_and_scoped_download(self):
        # Real WASI package, but explicitly NOT native launch/installation evidence.
        from app.runners.wasi import WasiRepackRunner
        from tests.test_api_contract import FIXTURE

        job_id, owner, auth = self.claim()
        container = self.app.state.container
        recipe = container.recipes.get(SUPPORTED_RECIPE)
        built = WasiRepackRunner(container.settings).run(FIXTURE, recipe, lambda *_: None)
        try:
            data = built.package_path.read_bytes()
            result = {
                "package": base64.b64encode(data).decode(),
                "verification": built.verification,
            }
            response = self.client.post(
                f"/v1/native-worker/{job_id}/complete", headers=auth, json=result
            )
            self.assertEqual(response.status_code, 200, response.text)
            saved = self.client.get(f"/v1/jobs/{job_id}", headers=owner).json()
            self.assertEqual(saved["verification"]["installation"]["state"], "not_run")
            grant = self.client.post(f"/v1/jobs/{job_id}/downloads/artifact", headers=owner).json()
            self.assertEqual(self.client.get(grant["url"]).content, data)
            self.assertEqual(self.client.get(grant["url"]).status_code, 404)
            report = self.client.post(f"/v1/jobs/{job_id}/downloads/report", headers=owner).json()
            self.assertEqual(
                self.client.get(report["url"]).json()["verification"], saved["verification"]
            )
        finally:
            container.bwrap_runner.cleanup(built)

    def test_static_validator_rejects_links_scripts_traversal_and_expansion(self):
        recipe = self.app.state.container.recipes.get(SUPPORTED_RECIPE)
        for name, kind in (
            ("../escape", tarfile.REGTYPE),
            ("usr/bin/demo", tarfile.SYMTYPE),
            (".INSTALL", tarfile.REGTYPE),
        ):
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as archive:
                member = tarfile.TarInfo(name)
                member.type = kind
                archive.addfile(member)
            with self.assertRaises(ValueError):
                validate_native_artifact(
                    zstandard.ZstdCompressor().compress(stream.getvalue()), recipe
                )
        with self.assertRaises(ValueError):
            validate_native_artifact(
                zstandard.ZstdCompressor().compress(b"x" * (1024 * 1024 + 1)), recipe
            )
