"""Approved package builds and the unique managed deployment admission lifecycle."""

import asyncio
import io
import json
import os
import tarfile
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import docker
from opsmesh_sdk.manifest import PackageManifest
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.packages import read_archive
from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    load_installation,
)
from opsmesh.extensions.models import Invocation, ManagedDeployment
from opsmesh.extensions.schemas import DeploymentRequest, extension_work
from opsmesh.extensions.service import audit_change
from opsmesh.identity.context import CurrentUser
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, InvalidArgumentError, NotFoundError
from opsmesh.temporal.dispatch.outbox import stage_execution, stage_signal

MAX_BUILD_BYTES = 128 * 1024 * 1024


def build_context(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, content in sorted(files.items()):
            entry = tarfile.TarInfo(name)
            entry.size = len(content)
            entry.mode = 0o644
            archive.addfile(entry, io.BytesIO(content))
    if output.tell() > MAX_BUILD_BYTES:
        raise InvalidArgumentError("The isolated build context exceeds its byte bound")
    return output.getvalue()


class DockerPackageBuilder:
    def __init__(self, *, image: str, sdk_wheel: Path) -> None:
        self.image, self.sdk_wheel = image, sdk_wheel

    async def build(self, payload: bytes, manifest: PackageManifest) -> str:
        return await asyncio.to_thread(self._build, payload, manifest)

    def _build(self, payload: bytes, manifest: PackageManifest) -> str:
        if not self.image or not self.sdk_wheel.is_file():
            raise ConflictError("Configure an approved builder image and SDK wheel")
        files = read_archive(payload)
        if "pyproject.toml" not in files:
            raise InvalidArgumentError("An installable package requires PyPA project metadata")
        expected_wheels = {f"wheels/{item.filename}" for item in manifest.execution.dependencies}
        actual_wheels = {name for name in files if name.startswith("wheels/")}
        if actual_wheels != expected_wheels:
            raise InvalidArgumentError("Offline wheels differ from the dependency lock")
        for wheel in manifest.execution.dependencies:
            if sha256(files[f"wheels/{wheel.filename}"]).hexdigest() != wheel.sha256:
                raise InvalidArgumentError("An offline dependency digest differs from its lock")
        context = {f"package/{name}": value for name, value in files.items()}
        context[f"sdk/{self.sdk_wheel.name}"] = self.sdk_wheel.read_bytes()
        client = docker.from_env()
        try:
            approved = client.images.get(self.image).id
            runtime_uid = os.getuid() if os.name == "posix" else 10001
            runtime_gid = os.getgid() if os.name == "posix" else 10001
            lines = [
                f"FROM {approved}",
                "USER root",
                "COPY sdk /build/sdk",
                "COPY package /build/package",
                "RUN python -m pip install --no-index --no-deps --force-reinstall /build/sdk/*.whl",
                "RUN mkdir -p /opt/extension && chown 65534:65534 /opt/extension "
                "&& chmod -R a+rX /build",
                "ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/extension",
                "USER 65534:65534",
            ]
            if expected_wheels:
                lines.append(
                    "RUN timeout --kill-after=2 300 python -m pip install --no-index --no-deps "
                    "--target /opt/extension /build/package/wheels/*.whl"
                )
            lines.extend(
                [
                    "RUN cp -R /build/package /tmp/package && timeout --kill-after=2 300 "
                    "python -m pip install "
                    "--no-index --no-deps --no-build-isolation "
                    "--target /opt/extension /tmp/package",
                    "USER root",
                    "RUN chown -hR 0:0 /opt/extension && chmod -R a-w /opt/extension "
                    "&& rm -rf /build /tmp/package /tmp/pip-* "
                    "&& mkdir -p /workspace "
                    f"&& chown {runtime_uid}:{runtime_gid} /workspace",
                    "ENTRYPOINT []",
                    f"USER {runtime_uid}:{runtime_gid}",
                    "WORKDIR /tmp",
                ]
            )
            context["Dockerfile"] = ("\n".join(lines) + "\n").encode()
            image, _ = client.images.build(
                fileobj=io.BytesIO(build_context(context)),
                custom_context=True,
                rm=True,
                forcerm=True,
                container_limits={"memory": manifest.execution.memory_mb * 1048576},
                labels={"opsmesh.package.sha256": sha256(payload).hexdigest()},
            )
            container = client.containers.create(
                image,
                ["python", "-I", "-m", "opsmesh_sdk.runtime.runner"],
                stdin_open=True,
                read_only=True,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                mem_limit=manifest.execution.memory_mb * 1048576,
                pids_limit=64,
                nano_cpus=int(manifest.execution.cpus * 1e9),
            )
            try:
                stream = container.attach_socket(params={"stdin": 1, "stream": 1})
                try:
                    container.start()
                    message = json.dumps({"action": "discover", "namespace": manifest.package_name})
                    cast(Any, stream)._sock.sendall((message + "\n").encode())
                    result = container.wait(timeout=manifest.execution.timeout_seconds)
                finally:
                    stream.close()
                data = container.logs(stdout=True, stderr=False, tail=1)
                if result["StatusCode"] != 0 or len(data) > 2097152:
                    raise ConflictError("Isolated entry point discovery failed")
                discovered: dict[str, Any] = json.loads(data)["result"]
                if PackageManifest.model_validate(discovered) != manifest:
                    raise ConflictError("Code exports differ from the frozen published manifest")
            finally:
                container.remove(force=True)
            return str(image.id)
        finally:
            client.close()


def request_start(
    session: Session,
    *,
    user: CurrentUser,
    instance_id: UUID,
    request: DeploymentRequest,
    scopes: ScopeAuthorization,
) -> ManagedDeployment:
    scopes.require_admission(session)
    instance = authorize_instance(
        session, instance_id, user=user, scopes=scopes, lock=True, execution=True
    )

    def existing(identifier: UUID) -> ManagedDeployment:
        return load_deployment(session, identifier)

    def create() -> ManagedDeployment:
        installation = load_installation(session, instance.installation_id)
        authorize_installation(session, installation, user=user)
        policy = PackageManifest.model_validate(installation.manifest).execution
        if (
            policy.mode != "managed_worker"
            or installation.status != "enabled"
            or instance.status != "enabled"
            or instance.revision != request.expected_revision
            or not installation.artifact
        ):
            raise ConflictError("The current instance is not eligible for a managed deployment")
        if (
            session.scalar(
                select(ManagedDeployment.id)
                .where(
                    ManagedDeployment.instance_id == instance.id,
                    ManagedDeployment.status != "stopped",
                )
                .limit(1)
            )
            is not None
        ):
            raise ConflictError("Stop and reconcile the existing managed deployment first")
        if (
            session.scalar(
                select(Invocation.id)
                .where(
                    Invocation.instance_id == instance.id,
                    Invocation.status == "needs_attention",
                )
                .limit(1)
            )
            is not None
        ):
            raise ConflictError(
                "Reconcile unresolved invocation effects before starting a new worker"
            )
        deployment = ManagedDeployment(
            id=uuid4(),
            instance_id=instance.id,
            instance_revision=instance.revision,
            artifact=installation.artifact,
            status="starting",
            revision=0,
        )
        session.add(deployment)
        session.flush()
        stage_execution(session, extension_work(deployment.id, "start_worker"))
        audit_change(session, user, "deployment.start_requested", deployment.id)
        return deployment

    return run_idempotent_create(
        session=session,
        scope_id=instance.id,
        actor_id=user.user_id,
        operation="extensions.deployment.start",
        idempotency_key=request.operation_id,
        request_payload=request.model_dump(mode="json"),
        get_existing=existing,
        create=create,
        resource_id=lambda deployment: deployment.id,
    )


def load_deployment(session: Session, identifier: UUID, *, lock: bool = False) -> ManagedDeployment:
    result = session.get(
        ManagedDeployment, identifier, with_for_update=lock, populate_existing=lock
    )
    if result is None:
        raise NotFoundError("Managed deployment not found")
    return result


def request_stop(
    session: Session,
    *,
    user: CurrentUser,
    deployment_id: UUID,
    expected_revision: int,
    scopes: ScopeAuthorization,
) -> ManagedDeployment:
    deployment = load_deployment(session, deployment_id)
    authorize_instance(session, deployment.instance_id, user=user, scopes=scopes, lock=True)
    deployment = load_deployment(session, deployment_id, lock=True)
    if deployment.status in {"stopping", "stopped"}:
        return deployment
    if deployment.revision != expected_revision:
        raise ConflictError("Deployment revision changed")
    stop_deployment(session, deployment=deployment, user=user)
    return deployment


def stop_deployment(session: Session, *, deployment: ManagedDeployment, user: CurrentUser) -> None:
    """Stage an already-authorized owner's cleanup in its enclosing transaction."""
    if deployment.status in {"stopping", "stopped"}:
        return
    deployment.status = "stopping"
    deployment.revision += 1
    stage_signal(
        session,
        dispatch_id=uuid4(),
        workflow_id=extension_work(deployment.id, "start_worker").workflow_id,
        signal_name="stop_worker",
        signal_args=(),
    )
    audit_change(session, user, "deployment.stop_requested", deployment.id)
