"""Kubernetes Job contract for production build isolation.

It deliberately does not silently fall back to local execution.  Deployments must
wire a trusted worker image plus private scoped input/output transfer before this
executor is selected; otherwise `submit` raises an explicit unavailable error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .bwrap import BuildEnvironmentUnavailable


@dataclass(frozen=True, slots=True)
class KubernetesBuildRequest:
    job_id: str
    image: str
    input_url: str
    output_callback_url: str
    artifact_prefix: str


class KubernetesJobRunner:
    """Render a least-privilege build Job; submission integration is deployment-owned."""

    def __init__(self, image: str | None = None):
        self.image = image

    def render_job(self, request: KubernetesBuildRequest) -> dict[str, Any]:
        if not request.image:
            raise BuildEnvironmentUnavailable("Kubernetes worker image is not configured.")
        return {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "generateName": "flexy-build-",
                "labels": {
                    "app.kubernetes.io/name": "flexy-build",
                    "app.kubernetes.io/component": "builder",
                    "flexy.io/job-id": request.job_id,
                },
            },
            "spec": {
                "backoffLimit": 0,
                "activeDeadlineSeconds": 300,
                "ttlSecondsAfterFinished": 300,
                "template": {
                    "metadata": {
                        "labels": {
                            "app.kubernetes.io/name": "flexy-build",
                            "app.kubernetes.io/component": "builder",
                        }
                    },
                    "spec": {
                        "restartPolicy": "Never",
                        "serviceAccountName": "flexy-builder",
                        "automountServiceAccountToken": False,
                        "enableServiceLinks": False,
                        "securityContext": {
                            "runAsNonRoot": True,
                            "runAsUser": 10001,
                            "runAsGroup": 10001,
                            "fsGroup": 10001,
                        },
                        "containers": [
                            {
                                "name": "builder",
                                "image": request.image,
                                "args": [
                                    "/app/run-build",
                                    "--input",
                                    request.input_url,
                                    "--callback",
                                    request.output_callback_url,
                                ],
                                "env": [
                                    {
                                        "name": "FLEXY_ARTIFACT_PREFIX",
                                        "value": request.artifact_prefix,
                                    },
                                    {"name": "HOME", "value": "/work"},
                                ],
                                "resources": {
                                    "requests": {
                                        "cpu": "1",
                                        "memory": "1Gi",
                                        "ephemeral-storage": "512Mi",
                                    },
                                    "limits": {
                                        "cpu": "1",
                                        "memory": "1Gi",
                                        "ephemeral-storage": "512Mi",
                                    },
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "readOnlyRootFilesystem": True,
                                    "privileged": False,
                                    "capabilities": {"drop": ["ALL"]},
                                    "seccompProfile": {"type": "RuntimeDefault"},
                                },
                                "volumeMounts": [{"name": "work", "mountPath": "/work"}],
                            }
                        ],
                        "volumes": [{"name": "work", "emptyDir": {"sizeLimit": "512Mi"}}],
                    },
                },
            },
        }

    def submit(self, request: KubernetesBuildRequest) -> str:
        # A client-side kubectl fallback would inherit host credentials and violates
        # the model.  Production integration must use a narrowly scoped controller.
        _ = self.render_job(request)
        raise BuildEnvironmentUnavailable(
            "Kubernetes executor requires the deployment's scoped Job controller "
            "and artifact callback integration."
        )
