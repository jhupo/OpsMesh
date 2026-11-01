"""Deterministic Job identity and restricted Pod specifications for one Run attempt."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.run_workers.kubernetes.configuration import (
    KubernetesWorkerConfiguration,
)


def identity(
    configuration: KubernetesWorkerConfiguration, run_id: str, generation: int
) -> tuple[str, str]:
    scope = f"{configuration.namespace}/{configuration.deployment_id}/{run_id}/{generation}"
    token = hashlib.sha256(scope.encode()).hexdigest()[:32]
    return f"opsmesh-run-{token[:24]}", token


def labels(
    configuration: KubernetesWorkerConfiguration, run_id: str | None = None
) -> dict[str, str]:
    result = {
        "app.kubernetes.io/name": "opsmesh-run",
        "opsmesh.io/deployment": configuration.deployment_id,
    }
    if run_id is not None:
        result["opsmesh.io/run"] = hashlib.sha256(run_id.encode()).hexdigest()[:32]
    return result


def selector(configuration: KubernetesWorkerConfiguration, run_id: str | None = None) -> str:
    return ",".join(f"{name}={value}" for name, value in labels(configuration, run_id).items())


def build_job(
    configuration: KubernetesWorkerConfiguration,
    *,
    run_id: str,
    generation: int,
    image: str,
    cpu: float,
    memory_mb: int,
) -> dict[str, Any]:
    name, token = identity(configuration, run_id, generation)
    resource_limits = {
        "cpu": str(cpu),
        "memory": f"{memory_mb}Mi",
        "ephemeral-storage": f"{configuration.ephemeral_storage_mb}Mi",
    }
    pod = {
        "serviceAccountName": configuration.service_account,
        "automountServiceAccountToken": False,
        "restartPolicy": "Never",
        "terminationGracePeriodSeconds": configuration.termination_grace_seconds,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 10001,
            "runAsGroup": 10001,
            "fsGroup": 10001,
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "containers": [
            {
                "name": "run",
                "image": image,
                "imagePullPolicy": configuration.image_pull_policy,
                "command": ["python", "-m", "opsmesh.entrypoints.run_worker"],
                "args": ["--run-id", run_id],
                "workingDir": "/var/lib/opsmesh",
                "env": [
                    {
                        "name": "OPSMESH_WORKER_SETTINGS",
                        "valueFrom": {
                            "secretKeyRef": {
                                "name": configuration.settings_secret,
                                "key": "OPSMESH_WORKER_SETTINGS",
                            }
                        },
                    },
                    {"name": "OPSMESH_RUN_LAUNCH_TOKEN", "value": token},
                    {"name": "PYTHONUNBUFFERED", "value": "1"},
                    {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                ],
                "envFrom": [
                    {"secretRef": {"name": name}} for name in configuration.environment_secrets
                ],
                "securityContext": {
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"]},
                },
                "resources": {"requests": resource_limits, "limits": resource_limits},
                "volumeMounts": [
                    {"name": "content", "mountPath": "/var/lib/opsmesh"},
                    {"name": "temporary", "mountPath": "/tmp"},
                ],
            }
        ],
        "volumes": [
            {"name": "content", "persistentVolumeClaim": {"claimName": configuration.data_claim}},
            {
                "name": "temporary",
                "emptyDir": {"sizeLimit": f"{configuration.ephemeral_storage_mb}Mi"},
            },
        ],
    }
    specification = {
        "backoffLimit": 0,
        "completions": 1,
        "parallelism": 1,
        "activeDeadlineSeconds": configuration.job_deadline_seconds,
        "ttlSecondsAfterFinished": configuration.ttl_seconds,
        "template": {"metadata": {"labels": labels(configuration, run_id)}, "spec": pod},
    }
    digest = hashlib.sha256(json.dumps(specification, sort_keys=True).encode()).hexdigest()
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": configuration.namespace,
            "labels": labels(configuration, run_id),
            "annotations": {
                "opsmesh.io/run-id": run_id,
                "opsmesh.io/launch-token": token,
                "opsmesh.io/generation": str(generation),
                "opsmesh.io/spec-fingerprint": digest,
            },
        },
        "spec": specification,
    }


def owned_identity(
    configuration: KubernetesWorkerConfiguration, job: dict[str, Any]
) -> tuple[str, str, int]:
    metadata = job.get("metadata", {})
    annotations = metadata.get("annotations", {})
    try:
        run_id, token = annotations["opsmesh.io/run-id"], annotations["opsmesh.io/launch-token"]
        generation = int(annotations["opsmesh.io/generation"])
        expected_name, expected_token = identity(configuration, run_id, generation)
        matches = (
            generation >= 0
            and metadata.get("name") == expected_name
            and token == expected_token
            and metadata.get("namespace") == configuration.namespace
            and all(
                metadata.get("labels", {}).get(k) == v
                for k, v in labels(configuration, run_id).items()
            )
        )
    except (KeyError, TypeError, ValueError) as error:
        raise OpsMeshError("OWNERSHIP_LOST", "Kubernetes Job identity is invalid") from error
    if not matches:
        raise OpsMeshError("OWNERSHIP_LOST", "Kubernetes Job is outside this deployment")
    return run_id, token, generation
