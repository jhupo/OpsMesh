"""Concrete owner bindings for extension references, native environments and public APIs."""

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID

import httpx
from fastapi import FastAPI
from fastapi.routing import APIRoute
from jsonschema import ValidationError as SchemaValidationError
from opsmesh_sdk.host.models import ModelPrompt
from opsmesh_sdk.manifest import PackageManifest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.routing import Match

from opsmesh.capabilities.publication import approved_versions
from opsmesh.extensions.access import authorize_instance, authorize_invocation, load_installation
from opsmesh.extensions.activities import ExtensionActivities
from opsmesh.extensions.contracts import HostRequest
from opsmesh.extensions.delegation import DelegatedCall, authenticated_call, current_call
from opsmesh.extensions.hooks import apply_chain
from opsmesh.extensions.invocation import admit_invocation, load_invocation
from opsmesh.extensions.models import ExtensionInstance, HostCallReceipt, Installation, Invocation
from opsmesh.extensions.sandbox import PackageSandbox
from opsmesh.extensions.schemas import (
    ExtensionReconcileRequest,
    HookInvocationContext,
    InstanceConfiguration,
    InvokeRequest,
    extension_work,
)
from opsmesh.identity.context import CurrentUser
from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.orchestration.contracts import ExecutionScope, ExecutionSubject
from opsmesh.orchestration.execution.contracts import (
    ExecutionRequest,
    InstalledExport,
    ToolConfiguration,
)
from opsmesh.orchestration.runs.model_operations import ModelOperation, ModelOperationRequest
from opsmesh.orchestration.runs.models import Run
from opsmesh.orchestration.runtime.access import environment_scope, load_environment
from opsmesh.orchestration.runtime.activities import RuntimeActivities
from opsmesh.orchestration.runtime.contracts import RuntimeCommand
from opsmesh.orchestration.runtime.models import EnvironmentOperationReceipt
from opsmesh.orchestration.runtime.service import stage_operation
from opsmesh.platform.model_connections.service import resolve_connection
from opsmesh.platform.operations.admission import is_open
from opsmesh.shared.db.idempotency import CreationReceipt
from opsmesh.shared.errors import ConflictError, DomainError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher


class ExtensionScopes:
    def __init__(self, admission: Any, cipher: SecretCipher) -> None:
        self.admission, self.cipher = admission, cipher

    def require_admission(self, session: Session) -> None:
        if not is_open(session):
            raise ConflictError("Extension execution admission is drained")

    def admit_instance(
        self,
        session: Session,
        *,
        user_id: UUID,
        owner_user_id: UUID,
        instance_id: UUID,
        configuration_id: UUID,
    ) -> None:
        from opsmesh.identity.users.service import load_identity
        from opsmesh.orchestration.configurations.bindings.models import CapabilityBinding
        from opsmesh.orchestration.configurations.schemas import ConfigurationSpec
        from opsmesh.orchestration.configurations.service import (
            authorize_configuration,
            get_configuration_revision,
        )

        actor = load_identity(session, user_id)
        if actor is None:
            raise ForbiddenError("The configuration user is unavailable")
        configuration = authorize_configuration(
            session, actor=actor, configuration_id=configuration_id, permission="use"
        )
        if configuration.owner_user_id != owner_user_id:
            raise ForbiddenError("The export is outside the configuration owner's grants")
        revision = get_configuration_revision(
            session, configuration_id=configuration.id, number=configuration.revision
        )
        specification = ConfigurationSpec.model_validate(revision.specification)
        binding_ids = [item.binding_id for item in specification.capabilities.values()]
        binding_ids.extend(item.binding_id for item in specification.hooks)
        for binding_id in binding_ids:
            binding = session.get(CapabilityBinding, binding_id)
            if (
                binding is not None
                and binding.owner_user_id == owner_user_id
                and binding.status == "enabled"
                and str(binding.configuration.get("instance_id")) == str(instance_id)
            ):
                return
        raise ForbiddenError("The configuration does not grant this installed export")

    def admit_source(
        self,
        session: Session,
        *,
        source_run_id: UUID | None,
        principal_id: UUID,
        configuration_id: UUID | None,
    ) -> None:
        if source_run_id is None:
            return
        from opsmesh.bootstrap.orchestration import ConfigurationRunSources

        source = session.get(Run, source_run_id)
        expected_scope = configuration_id or principal_id
        expected_owner = (
            f"configuration:{configuration_id}" if configuration_id else (f"user:{principal_id}")
        )
        if (
            source is None
            or source.principal_id != principal_id
            or source.scope_id != expected_scope
            or source.owner_ref != expected_owner
            or source.status not in {"running", "finalizing"}
        ):
            raise ForbiddenError("The Invocation source Run is no longer available")
        ConfigurationRunSources(self.cipher, self.admission, self).authorize(
            session, run_id=source_run_id
        )

    def admit(
        self,
        session: Session,
        *,
        user_id: UUID,
        configuration_id: UUID | None,
        execution: bool = False,
    ) -> None:
        self.admission.admit(
            session,
            subject=ExecutionSubject(user_id),
            scope=ExecutionScope(
                configuration_id=configuration_id,
            ),
        )


def invocation_scope(snapshot: dict[str, Any]) -> ExecutionScope:
    return ExecutionScope(
        configuration_id=UUID(str(snapshot["configuration_id"]))
        if snapshot.get("configuration_id")
        else None,
    )


class ExtensionModelAuthorization:
    def __init__(self, scopes: ExtensionScopes) -> None:
        self.scopes = scopes

    def authorize_submission(
        self, session: Session, *, principal_id: UUID, request: ModelOperationRequest
    ) -> None:
        proof = current_call()
        if (
            proof.invocation_id != request.source_ref
            or proof.operation != f"host.models.{request.kind}"
        ):
            raise ForbiddenError("The model operation requires its authenticated host delegation")

    def admit(
        self, session: Session, *, principal_id: UUID, request: ModelOperationRequest
    ) -> None:
        invocation = load_invocation(session, request.source_ref, lock=True)
        user, instance, _ = authorize_invocation(
            session,
            invocation,
            users=UserIdentityDirectory(),
            scopes=self.scopes,
            permission=f"host.models.{request.kind}",
        )
        reference = f"{request.connection_id}:{request.model}"
        if (
            invocation.principal_id != principal_id
            or reference not in instance.model_connections
            or instance.configuration_id != request.configuration_id
        ):
            raise ForbiddenError("The model operation is outside the current Invocation grants")
        resolve_connection(
            session, actor=user, connection_id=request.connection_id, model=request.model
        )


class ExtensionReconciliation:
    def __init__(self, cipher: SecretCipher) -> None:
        self.cipher = cipher

    def verify(
        self,
        session: Session,
        invocation: Invocation,
        receipt: HostCallReceipt,
        resolution: ExtensionReconcileRequest,
    ) -> None:
        request = self.cipher.decrypt_payload(receipt.request_ciphertext)
        if len(str(resolution.model_dump()).encode()) > 1048576:
            raise InvalidArgumentError("The reconciliation exceeds its byte bound")
        if request["path"] not in {"/host/models/complete", "/host/models/embed"}:
            # Explicit owner evidence closes unknown API effects; it never
            # grants permission to repeat the original external request.
            return
        body = request.get("body")
        if not isinstance(body, dict):
            raise InvalidArgumentError("The recorded model request has no body")
        reference = str(body["connection_ref"])
        instance = session.get(ExtensionInstance, invocation.instance_id)
        if instance is None or reference not in instance.model_connections:
            raise ForbiddenError("The model reference is outside the instance grants")
        configuration_id = invocation_scope(invocation.snapshot).configuration_id
        key = f"extension:{invocation.id}:{receipt.operation_id}"
        identity = (
            configuration_id or invocation.principal_id,
            invocation.principal_id,
            "orchestration.model_operation",
            sha256(key.encode()).hexdigest(),
        )
        lock_digest = sha256("|".join(map(str, identity)).encode()).digest()
        lock_id = int.from_bytes(lock_digest[:8], "big", signed=True)
        if not session.scalar(select(func.pg_try_advisory_xact_lock(lock_id))):
            raise ConflictError("The model operation submission is still in progress")
        creation = session.get(CreationReceipt, identity)
        if creation is None:
            if resolution.outcome != "not_executed":
                raise ConflictError("No model owner receipt confirms this result")
            return
        model = session.get(ModelOperation, creation.resource_id)
        if model is None or model.source_ref != invocation.id:
            raise ConflictError("The model owner receipt does not match this Invocation")
        if model.status not in {"completed", "failed"}:
            raise ConflictError("Resolve the model operation and its ledgers first")
        if model.status == "completed":
            actual = {
                "status": 200,
                "body": self.cipher.decrypt_payload(model.result_ciphertext or ""),
            }
            if resolution.outcome != "completed" or resolution.result != actual:
                raise ConflictError("The host result differs from the model owner receipt")
        elif resolution.outcome != "not_executed":
            raise ConflictError("The model owner has no confirmed result")


class RunExtensions:
    def __init__(
        self,
        scopes: ExtensionScopes,
        cipher: SecretCipher,
        sessions: sessionmaker[Session] | None = None,
    ) -> None:
        self.scopes, self.cipher, self.sessions = scopes, cipher, sessions
        self.activities: ExtensionActivities | None = None

    def validate(
        self,
        session: Session,
        *,
        principal_id: UUID,
        scope: ExecutionScope,
        configuration: dict[str, Any],
    ) -> None:
        request = ExecutionRequest.model_validate(configuration)
        references: list[tuple[InstalledExport | None, ToolConfiguration | None]] = [
            (tool.extension, tool)
            for agent in request.agents.values()
            for tool in agent.tools
            if tool.extension is not None
        ]
        references.extend((hook, None) for hook in request.hooks)
        user = UserIdentityDirectory().load_identity(session, principal_id)
        if user is None:
            raise ForbiddenError("The execution principal is unavailable")
        for reference, tool in references:
            assert reference is not None
            instance = authorize_instance(
                session, reference.instance_id, user=user, scopes=self.scopes, execution=True
            )
            installation = load_installation(session, reference.installation_id)
            if (
                instance.installation_id != installation.id
                or installation.status != "enabled"
                or instance.status != "enabled"
                or instance.revision != reference.configuration_revision
                or installation.artifact != reference.artifact
                or ExecutionScope(
                    configuration_id=instance.configuration_id,
                )
                != scope
            ):
                raise ForbiddenError(
                    "The installed execution reference changed or is outside the Run scope"
                )
            manifest = PackageManifest.model_validate(installation.manifest)
            if tool is not None:
                export = next(
                    (
                        value
                        for value in manifest.operations
                        if value.export_id == reference.export_id
                    ),
                    None,
                )
                if export is None or export.input_schema.model_dump_schema() != tool.input_schema:
                    raise ForbiddenError("The tool schema differs from the installed export")
            else:
                hook_export = next(
                    (value for value in manifest.hooks if value.export_id == reference.export_id),
                    None,
                )
                hook = next(value for value in request.hooks if value == reference)
                if (
                    hook_export is None
                    or not set(hook.stages) <= set(hook_export.stages)
                    or hook.failure_policy != hook_export.failure_policy
                ):
                    raise ForbiddenError("The hook binding differs from its installed declaration")

    async def invoke(
        self,
        *,
        run_id: UUID,
        operation_id: str,
        reference: dict[str, Any],
        arguments: dict[str, Any],
        kind: str = "operation",
    ) -> dict[str, Any]:
        if self.sessions is None or self.activities is None:
            raise ConflictError("The installed execution Activity is not assembled")
        with self.sessions() as session, session.begin():
            run = session.get(Run, run_id, with_for_update=True)
            if run is None or run.status not in {"running", "finalizing"}:
                raise ForbiddenError("The Run is not active for installed execution")
            request = ExecutionRequest.model_validate(
                self.cipher.decrypt_payload(run.request_ciphertext)
            )
            frozen = InstalledExport.model_validate(
                {key: reference[key] for key in InstalledExport.model_fields}
            )
            allowed = [tool.extension for agent in request.agents.values() for tool in agent.tools]
            if kind == "hook":
                allowed = [
                    InstalledExport.model_validate(
                        hook.model_dump(include=set(InstalledExport.model_fields))
                    )
                    for hook in request.hooks
                ]
            if frozen not in allowed:
                raise ForbiddenError("The installed reference is absent from the frozen Run")
            hook_binding = None
            if kind == "hook":
                hook_binding = next(
                    (
                        hook
                        for hook in request.hooks
                        if hook.binding_id == reference.get("binding_id")
                    ),
                    None,
                )
                if hook_binding is None:
                    raise ForbiddenError("The hook binding is absent from the frozen Run")
            scope = ExecutionScope(
                configuration_id=None if run.owner_ref.startswith("user:") else run.scope_id,
            )
            self.validate(
                session,
                principal_id=run.principal_id,
                scope=scope,
                configuration=request.model_dump(mode="json"),
            )
            user = UserIdentityDirectory().load_identity(session, run.principal_id)
            assert user is not None
            invocation = admit_invocation(
                session,
                user=user,
                instance_id=frozen.instance_id,
                request=InvokeRequest(
                    export_id=frozen.export_id, operation_id=operation_id, arguments=arguments
                ),
                scopes=self.scopes,
                cipher=self.cipher,
                parent_id=request.parent_invocation_id,
                source_run_id=run_id,
                kind=kind,
                dispatch=False,
                hook_context=HookInvocationContext(
                    run_id=run_id,
                    binding_id=hook_binding.binding_id,
                    **hook_binding.budget.model_dump(),
                )
                if hook_binding
                else None,
            )
            identifier = invocation.id
            if invocation.status == "completed":
                return self.cipher.decrypt_payload(invocation.result_ciphertext or "")
        answer = await self.activities.invoke(extension_work(identifier, "invoke"))
        if answer["status"] != "completed":
            raise ConflictError("The installed operation did not produce a confirmed result")
        with self.sessions() as session:
            invocation = load_invocation(session, identifier)
            return self.cipher.decrypt_payload(invocation.result_ciphertext or "")

    async def hooks(
        self,
        *,
        run_id: UUID,
        occurrence: str,
        stage: str,
        payload: dict[str, Any],
        entered: list[str] | None = None,
    ) -> dict[str, Any]:
        if self.sessions is None:
            raise ConflictError("The hook Activity is not assembled")
        with self.sessions() as session:
            run = session.get(Run, run_id)
            if run is None:
                raise ForbiddenError("The hook Run is unavailable")
            request = ExecutionRequest.model_validate(
                self.cipher.decrypt_payload(run.request_ciphertext)
            )
            bindings: list[dict[str, Any]] = []
            for hook in request.hooks:
                installation = load_installation(session, hook.installation_id)
                manifest = PackageManifest.model_validate(installation.manifest)
                export = next(
                    value for value in manifest.hooks if value.export_id == hook.export_id
                )
                bindings.append(
                    {**hook.model_dump(mode="json"), "patch_fields": list(export.patch_fields)}
                )

        async def invoke(
            binding: dict[str, Any], arguments: dict[str, Any], key: str
        ) -> dict[str, Any]:
            reference = {key: binding[key] for key in InstalledExport.model_fields}
            reference["binding_id"] = binding["binding_id"]
            return await self.invoke(
                run_id=run_id,
                operation_id=key,
                reference=reference,
                arguments=arguments,
                kind="hook",
            )

        return await apply_chain(
            stage=stage,
            occurrence=occurrence,
            payload=payload,
            bindings=bindings,
            invoke=invoke,
            entered=entered,
        )


class ExtensionReferences:
    def __init__(self, admission: Any) -> None:
        self.admission = admission

    def validate(
        self,
        session: Session,
        *,
        user: CurrentUser,
        configuration: InstanceConfiguration,
        artifact: str,
    ) -> None:
        scope = ExecutionScope(
            configuration_id=configuration.configuration_id,
        )
        if configuration.environment_id is not None:
            environment = load_environment(
                session,
                environment_id=configuration.environment_id,
                subject=ExecutionSubject(user.user_id),
                admission=self.admission,
            )
            if (
                environment.status != "ready"
                or environment_scope(environment) != scope
                or environment.configuration.get("image") != artifact
            ):
                raise ForbiddenError(
                    "The instance requires a ready environment with this package artifact and scope"
                )
        for reference in configuration.model_connections:
            identifier, model = reference.split(":", 1)
            resolve_connection(session, actor=user, connection_id=UUID(identifier), model=model)


def installed_images(session: Session, user: CurrentUser) -> frozenset[str]:
    from opsmesh.capabilities.models import CapabilityDefinition, CapabilityVersion

    return frozenset(
        session.scalars(
            select(Installation.artifact)
            .join(CapabilityVersion, CapabilityVersion.id == Installation.version_id)
            .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityVersion.definition_id)
            .where(
                Installation.status == "enabled",
                Installation.artifact.is_not(None),
                CapabilityDefinition.status == "active",
                (
                    (CapabilityDefinition.visibility == "public")
                    & (CapabilityVersion.status == "published")
                    & (
                        (CapabilityDefinition.source == "system")
                        | CapabilityVersion.id.in_(approved_versions(session))
                    )
                )
                | (CapabilityDefinition.created_by_user_id == user.user_id)
                | user.has_platform_role("admin"),
            )
        )
    )


class PackageEnvironmentOperations:
    """Adapt extension commands to the environment owner's existing effect receipts."""

    def __init__(self, runtime: RuntimeActivities) -> None:
        self.runtime = runtime

    async def claim(
        self,
        *,
        environment_id: UUID,
        principal_id: UUID,
        configuration_id: UUID | None,
        invocation_id: UUID,
        operation_id: str,
        artifact: str,
        fingerprint: str,
        command: list[str],
        timeout_seconds: int,
    ) -> tuple[bool, dict[str, Any]]:
        work = RuntimeCommand(
            execution_id=invocation_id,
            workflow_id="",
            environment_id=environment_id,
            principal_id=principal_id,
            operation_id=operation_id,
            action="command",
            arguments={"command": command, "timeout_seconds": timeout_seconds},
        )

        def prepare() -> tuple[bool, dict[str, Any]]:
            with self.runtime.sessions() as session, session.begin():
                record = load_environment(
                    session,
                    environment_id=environment_id,
                    subject=ExecutionSubject(principal_id),
                    admission=self.runtime.admission,
                    lock=True,
                    shared_use=True,
                )
                if (
                    environment_scope(record) != ExecutionScope(configuration_id=configuration_id)
                    or record.configuration.get("image") != artifact
                ):
                    raise ForbiddenError("The environment differs from the fixed package and scope")
                receipt = session.get(EnvironmentOperationReceipt, (record.scope_id, operation_id))
                if receipt is not None:
                    if (
                        receipt.environment_id != environment_id
                        or receipt.action != "command"
                        or receipt.fingerprint != fingerprint
                    ):
                        raise ConflictError("The package operation identity has different input")
                else:
                    if record.status != "ready" or record.active_operation_id is not None:
                        raise ConflictError("The package environment has an unfinished operation")
                    stage_operation(
                        session,
                        record=record,
                        subject=ExecutionSubject(principal_id),
                        operation_id=operation_id,
                        action="command",
                        arguments=work.arguments,
                        fingerprint=fingerprint,
                        dispatch=False,
                    )
            try:
                return self.runtime.claim(work)
            except DomainError as exc:
                self.runtime.reject_pending(work, exc.code)
                raise

        return await asyncio.to_thread(prepare)

    async def container_id(self, state: dict[str, Any]) -> str:
        native = await self.runtime.backend.resume(state)
        identifier = self.runtime.backend.state(native).get("container_id")
        if not isinstance(identifier, str) or not identifier:
            raise ConflictError("The native stdio transport is unavailable")
        return identifier

    async def complete(
        self,
        *,
        environment_id: UUID,
        principal_id: UUID,
        invocation_id: UUID,
        operation_id: str,
        result: dict[str, Any] | None,
    ) -> None:
        work = RuntimeCommand(
            execution_id=invocation_id,
            workflow_id="",
            environment_id=environment_id,
            principal_id=principal_id,
            operation_id=operation_id,
            action="command",
        )
        await asyncio.to_thread(self.runtime.complete, work, result)


class ExtensionSandbox:
    def __init__(self, sessions: sessionmaker[Session], sandbox: PackageSandbox) -> None:
        self.sessions, self.sandbox = sessions, sandbox

    async def invoke(
        self, *, invocation_id: UUID, envelope: dict[str, Any], host: HostRequest
    ) -> dict[str, Any]:
        with self.sessions() as session:
            invocation = load_invocation(session, invocation_id)
            snapshot: dict[str, Any] = dict(invocation.snapshot)
            arguments: dict[str, Any] = dict(
                environment_id=UUID(str(snapshot["environment_id"])),
                principal_id=invocation.principal_id,
                configuration_id=invocation_scope(snapshot).configuration_id,
                operation_id=f"extension:{invocation.id}",
                artifact=str(snapshot["artifact"]),
                envelope=envelope,
                deadline=invocation.deadline,
                host=host,
                memory_mb=snapshot["execution_policy"]["memory_mb"],
                cpus=snapshot["execution_policy"]["cpus"],
            )
        return await self.sandbox.execute(**arguments)


PUBLIC_OPERATIONS = frozenset(
    {
        "channels.accounts.inbox.receive",
        "channels.accounts.connection.read",
        "channels.deliveries.claim",
        "channels.deliveries.result",
        "orchestration.sessions.create",
        "orchestration.sessions.get",
        "orchestration.sessions.close",
        "orchestration.sessions.history.list",
        "orchestration.runs.create",
        "orchestration.runs.get",
        "orchestration.runs.cancel",
        "orchestration.runs.operations",
        "orchestration.runs.events.list",
        "orchestration.model_operations.create",
        "orchestration.model_operations.get",
        "platform.audit.record",
        "platform.audit.list",
        "platform.audit.read",
        "resources.files.read",
        "resources.files.versions.list",
        "resources.files.list",
        "resources.files.create",
        "resources.files.content.write",
        "resources.files.delete",
        "resources.files.directories.create",
        "resources.files.directories.list",
        "orchestration.configurations.files.list",
        "orchestration.configurations.files.create",
        "orchestration.configurations.files.content",
        "orchestration.configurations.files.write",
    }
)


class ExtensionPublicAPI:
    def __init__(
        self,
        app: FastAPI,
        sessions: sessionmaker[Session],
        scopes: ExtensionScopes,
        cipher: SecretCipher,
    ) -> None:
        self.app, self.sessions, self.scopes, self.cipher = app, sessions, scopes, cipher
        self.users = UserIdentityDirectory()

    async def request(
        self, *, invocation_id: UUID, operation_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        method, path = request["method"], request["path"]
        if path == "/host/jobs" or path.startswith("/host/jobs/"):
            from opsmesh.bootstrap.orchestration import host_jobs

            return await asyncio.to_thread(
                host_jobs,
                self.app.state.flow_activities,
                invocation_id=invocation_id,
                operation_id=operation_id,
                request=request,
            )
        if path in {"/host/models/complete", "/host/models/embed"} and method == "POST":
            return await self.model_request(invocation_id, operation_id, request)
        with self.sessions() as session:
            invocation = load_invocation(session, invocation_id)
            route, parameters = self.match(method, path)
            operation = route.operation_id
            if operation not in PUBLIC_OPERATIONS:
                raise ForbiddenError("The API operation is not available to extension delegations")
            permission = operation
            if operation and operation.startswith("platform.audit."):
                permission = "audit.append" if operation.endswith("record") else "audit.read"
            if operation and operation.startswith("orchestration.model_operations."):
                kind = request.get("_host_model_kind")
                if kind not in {"complete", "embed"}:
                    raise ForbiddenError(
                        "Model operations require the current host model delegation"
                    )
                permission = f"host.models.{kind}"
                if method == "POST":
                    if request["body"].get("source_ref") != str(invocation_id):
                        raise ForbiddenError("Model source reference differs from the Invocation")
                else:
                    model_operation = session.get(
                        ModelOperation, UUID(str(parameters["operation_id"]))
                    )
                    if model_operation is None or model_operation.source_ref != invocation_id:
                        raise ForbiddenError("The model operation belongs to another Invocation")
            user, instance, installation = authorize_invocation(
                session, invocation, users=self.users, scopes=self.scopes, permission=permission
            )
            body = request.get("body") or {}
            if operation == "orchestration.runs.create":
                body = {**body, "parent_invocation_id": str(invocation.id)}
            if operation == "platform.audit.record":
                body = dict(body)
                manifest = PackageManifest.model_validate(installation.manifest)
                declaration = next(
                    (item for item in manifest.audit_actions if item.action == body.get("action")),
                    None,
                )
                if declaration is None or body.get("target_type") not in declaration.target_types:
                    raise ForbiddenError("The package does not declare this audit action or target")
                try:
                    declaration.details_schema.validate_instance(body.get("details", {}))
                except SchemaValidationError as error:
                    raise InvalidArgumentError(
                        "Audit details violate the declared schema"
                    ) from error
                body["action"] = f"{installation.namespace}.{declaration.action}"
                body["target_type"] = f"{installation.namespace}.{body['target_type']}"
                if body.get("evidence_refs"):
                    raise ForbiddenError(
                        "Platform evidence references require an authorized resource binding"
                    )
            if (
                operation
                and operation.startswith("platform.audit.")
                and method == "GET"
                and "event_id" in parameters
            ):
                from opsmesh.platform.audit.models import AuditEvent

                event = session.get(AuditEvent, UUID(str(parameters["event_id"])))
                if event is None or event.instance_id != instance.id:
                    raise ForbiddenError("The audit event belongs to another instance")
            query = dict(request.get("query") or {})
            self.validate_scope(
                session,
                operation,
                parameters,
                body,
                query,
                invocation.snapshot,
                principal_id=user.user_id,
            )
            headers = {
                "X-OpsMesh-Installation-Id": str(installation.id),
                "X-OpsMesh-Instance-Id": str(instance.id),
                "X-OpsMesh-Invocation-Id": str(invocation.id),
                "X-OpsMesh-Namespace": installation.namespace,
            }
        if method != "GET":
            query["idempotency_key"] = f"extension:{invocation_id}:{operation_id}"
        with authenticated_call(DelegatedCall(invocation_id, permission, method, path)):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url="http://opsmesh.internal"
            ) as client:
                response = await client.request(
                    method,
                    path,
                    params=query,
                    json=body,
                    headers=headers,
                    timeout=request["timeout_seconds"],
                )
        if len(response.content) > 2097152:
            raise InvalidArgumentError("The delegated API response exceeds its byte bound")
        response_body = response.json() if response.content else None
        if operation == "platform.audit.list" and response.is_success:
            assert isinstance(response_body, dict)
            response_body["events"] = [
                item
                for item in response_body["events"]
                if item.get("instance_id") == str(instance.id)
            ]
        return {
            "status": response.status_code,
            "body": response_body,
        }

    async def model_request(
        self, invocation_id: UUID, operation_id: str, request: dict[str, Any]
    ) -> dict[str, Any]:
        body = request.get("body") or {}
        kind = request["path"].rsplit("/", 1)[-1]
        with self.sessions() as session:
            invocation = load_invocation(session, invocation_id)
            user, instance, _ = authorize_invocation(
                session,
                invocation,
                users=self.users,
                scopes=self.scopes,
                permission=f"host.models.{kind}",
            )
            reference = body.get("connection_ref")
            if reference not in instance.model_connections:
                raise ForbiddenError("The instance does not grant this model connection")
            connection, model = reference.split(":", 1)
            resolve_connection(session, actor=user, connection_id=UUID(connection), model=model)
            payload: dict[str, Any] = {
                "configuration_id": str(instance.configuration_id)
                if instance.configuration_id
                else None,
                "connection_id": connection,
                "model": model,
                "kind": kind,
                "source_ref": str(invocation_id),
            }
            if kind == "complete":
                prompt = ModelPrompt.model_validate(body.get("prompt"))
                if any(
                    block.block_type != "text" or block.text is None or block.ref is not None
                    for block in prompt.blocks
                ):
                    raise InvalidArgumentError("One-shot completion accepts explicit text blocks")
                payload.update(
                    inputs=[block.text for block in prompt.blocks],
                    max_output_tokens=prompt.max_output_tokens or 4096,
                    temperature=prompt.temperature,
                    reasoning_effort=prompt.reasoning_effort,
                )
            else:
                payload["inputs"] = body.get("inputs")
            deadline = invocation.deadline
        path = f"{self.app.state.settings.api_prefix}/orchestration/model-operations"
        created = await self.request(
            invocation_id=invocation_id,
            operation_id=operation_id,
            request={
                "method": "POST",
                "path": path,
                "body": payload,
                "query": {},
                "timeout_seconds": 30,
                "_host_model_kind": kind,
            },
        )
        if created["status"] >= 400:
            return created
        identifier = created["body"]["id"]
        while datetime.now(UTC) < deadline:
            answer = await self.request(
                invocation_id=invocation_id,
                operation_id=operation_id,
                request={
                    "method": "GET",
                    "path": f"{path}/{identifier}",
                    "query": {},
                    "timeout_seconds": 30,
                    "_host_model_kind": kind,
                },
            )
            if answer["status"] >= 400:
                return answer
            status = answer["body"]["status"]
            if status == "completed":
                return {"status": 200, "body": answer["body"]["result"]}
            if status in {"failed", "needs_attention", "cancelled"}:
                raise ConflictError("The model operation did not produce a confirmed result")
            await asyncio.sleep(0.1)
        raise ConflictError("The Invocation expired while waiting for its model operation")

    def match(self, method: str, path: str) -> tuple[APIRoute, dict[str, Any]]:
        if "?" in path or "#" in path or ".." in path or "%" in path:
            raise InvalidArgumentError("Delegated paths must be canonical API paths")
        for route in self.app.routes:
            if isinstance(route, APIRoute):
                match, child = route.matches({"type": "http", "method": method, "path": path})
                if match == Match.FULL:
                    return route, child.get("path_params", {})
        raise ForbiddenError("The delegated API route is not available")

    @staticmethod
    def validate_scope(
        session: Session,
        operation: str | None,
        parameters: dict[str, Any],
        body: dict[str, Any],
        query: dict[str, str],
        snapshot: dict[str, Any],
        *,
        principal_id: UUID,
    ) -> None:
        from opsmesh.orchestration.runs.models import Run
        from opsmesh.orchestration.sessions.access import record_scope
        from opsmesh.orchestration.sessions.models import SessionRecord

        expected = invocation_scope(snapshot)
        target = None
        if operation and operation.startswith("orchestration.configurations.files."):
            target = ExecutionScope(configuration_id=UUID(str(parameters["configuration_id"])))
        if operation and operation.startswith("channels."):
            from opsmesh.channels.models import ChannelAccount, ChannelDelivery

            account_id = parameters.get("account_id")
            if "delivery_id" in parameters:
                delivery = session.get(ChannelDelivery, UUID(str(parameters["delivery_id"])))
                account_id = delivery.account_id if delivery else None
            account = session.get(ChannelAccount, UUID(str(account_id))) if account_id else None
            if account is None:
                raise ForbiddenError("The delegated channel account is unavailable")
            target = ExecutionScope(
                configuration_id=account.configuration_id,
            )
        if "run_id" in parameters:
            record = session.get(Run, UUID(str(parameters["run_id"])))
            if record is None or record.principal_id != principal_id:
                raise ForbiddenError("The delegated Run is unavailable")
            target = ExecutionScope(
                configuration_id=None if record.owner_ref.startswith("user:") else record.scope_id,
            )
        session_id = parameters.get("session_id") or body.get("session_id")
        if session_id:
            model_session = session.get(SessionRecord, UUID(str(session_id)))
            if model_session is None or model_session.principal_id != principal_id:
                raise ForbiddenError("The delegated model Session is unavailable")
            target = record_scope(model_session, principal_id=principal_id)
        if operation == "orchestration.sessions.create":
            target = invocation_scope(body)
        if target is not None and target != expected:
            raise ForbiddenError("The delegated API target is outside the Invocation scope")
        if operation and operation.startswith("platform.audit."):
            if operation.endswith("record"):
                return None
            if expected.is_user_scope:
                if operation != "platform.audit.read" or "event_id" not in parameters:
                    raise ForbiddenError("Unscoped audit listing is outside the Invocation scope")
                from opsmesh.platform.audit.models import AuditEvent

                event = session.get(AuditEvent, UUID(str(parameters["event_id"])))
                if (
                    event is None
                    or event.configuration_id is not None
                    or event.actor_id != str(principal_id)
                ):
                    raise ForbiddenError("The audit event is outside the Invocation scope")
                # Instance ownership is checked by request(); the public API still
                # enforces the current user's audit-read permission.
                return None
            if "event_id" in parameters:
                from opsmesh.platform.audit.models import AuditEvent

                event = session.get(AuditEvent, UUID(str(parameters["event_id"])))
                if event is None or event.configuration_id != expected.configuration_id:
                    raise ForbiddenError("The audit event is outside the Invocation scope")
            query["configuration_id"] = str(expected.configuration_id)
        return None
