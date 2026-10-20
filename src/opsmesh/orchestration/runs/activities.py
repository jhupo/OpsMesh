"""Composition-owned state and explicit execution operations."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from jsonschema import Draft202012Validator
from opsmesh_sdk.manifest import PackageManifest
from sqlalchemy import or_, select, true
from sqlalchemy.orm import Session, sessionmaker
from temporalio import activity

from opsmesh.extensions.access import (
    ScopeAuthorization,
    authorize_installation,
    authorize_instance,
    installation_viewer,
    load_installation,
)
from opsmesh.extensions.invocation import admit_invocation, cancel_invocation
from opsmesh.extensions.models import Invocation
from opsmesh.extensions.schemas import InvokeRequest
from opsmesh.identity.auth.contracts import UserDirectory
from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.access import authorize_scope
from opsmesh.orchestration.contracts import (
    ExecutionBindings,
    ExecutionSubject,
    RunAssets,
    ScopeAdmission,
    configuration_access_for,
)
from opsmesh.orchestration.definitions.contracts import (
    TERMINAL_FLOW_STATUSES,
    FlowDefinitionSpec,
    FlowExecutionPolicy,
    FlowGraph,
    FlowLimits,
    FlowNode,
    FlowProjection,
    StepCommand,
)
from opsmesh.orchestration.definitions.models import DefinitionRevision
from opsmesh.orchestration.definitions.service import load_revision
from opsmesh.orchestration.execution.contracts import ExecutionRequest, PreparedRun, RunReference
from opsmesh.orchestration.runs.access import (
    OwnedAgentCancellation,
    load_flow_run,
    load_run,
    lock_run_facts,
)
from opsmesh.orchestration.runs.contracts import ApprovalCheckpoint, RunClaim, RunCompletion
from opsmesh.orchestration.runs.events import append_event, append_flow_event
from opsmesh.orchestration.runs.models import (
    RUN_ACTIVE_STATUSES,
    FlowCommand,
    FlowPlanApplication,
    FlowProjectionReceipt,
    FlowRun,
    FlowSignalReceipt,
    OperationReceipt,
    Run,
    RunApproval,
    StepAttempt,
)
from opsmesh.orchestration.runs.scheduling import (
    acquire_capacity,
    is_session_head,
    release_capacity,
)
from opsmesh.orchestration.runs.schemas import (
    CancelRequest,
    FlowControl,
    FlowPlanProposal,
    FlowPlanReview,
    FlowRunCreate,
)
from opsmesh.orchestration.runs.service import cancel as cancel_agent
from opsmesh.orchestration.runs.service import (
    control_run,
    node_at_path,
    record_command,
    start_run,
    validate_plan_request,
)
from opsmesh.orchestration.runs.usage import retain_uncertain
from opsmesh.orchestration.runtime.models import EnvironmentOperationReceipt
from opsmesh.orchestration.sessions.access import record_scope
from opsmesh.orchestration.sessions.history import commit_run, prepare_run
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.shared.approvals import (
    ApprovalSnapshot,
    ApprovalTarget,
    approvals_for,
    effective_status,
)
from opsmesh.shared.errors import ConflictError, DomainError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.policies import enforce_operation_policy
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import payload_digest


class FlowActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        users: UserDirectory,
        scopes: ScopeAuthorization,
        admission: ScopeAdmission,
        cipher: SecretCipher,
        assets: RunAssets,
        bindings: ExecutionBindings,
    ) -> None:
        self.sessions, self.users, self.scopes = sessions, users, scopes
        self.admission, self.cipher, self.assets, self.bindings = (
            admission,
            cipher,
            assets,
            bindings,
        )

    def actor(self, session: Session, run: FlowRun) -> CurrentUser:
        initiator = self.users.load_identity(session, run.initiated_by_id)
        if initiator is None or initiator.status != "active":
            raise ForbiddenError("The flow initiator is no longer active")
        authorize_scope(
            session,
            actor=initiator,
            owner_id=run.principal_id,
            configuration_id=run.configuration_id,
            write=True,
        )
        user = self.users.load_identity(session, run.principal_id)
        if user is None or user.status != "active":
            raise ForbiddenError("The flow principal is no longer active")
        return user

    def validate_node(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        run: FlowRun,
        node: FlowNode,
        seen: set[UUID] | None = None,
    ) -> None:
        if node.kind == "operation":
            reference = node.export
            assert reference is not None
            instance = authorize_instance(
                session, reference.instance_id, user=actor, scopes=self.scopes
            )
            installation = load_installation(session, reference.installation_id)
            authorize_installation(
                session, installation, user=installation_viewer(session, instance)
            )
            if (
                instance.configuration_id != run.configuration_id
                or instance.installation_id != installation.id
                or instance.status != "enabled"
                or installation.status != "enabled"
                or instance.revision != reference.configuration_revision
                or installation.artifact != reference.artifact
            ):
                raise ForbiddenError("The flow export is revoked or outside its frozen scope")
            export = next(
                (
                    item
                    for item in PackageManifest.model_validate(installation.manifest).operations
                    if item.export_id == reference.export_id
                ),
                None,
            )
            if export is None or not set(export.permissions) <= (
                set(instance.permissions) & set(installation.permission_ceiling)
            ):
                raise ForbiddenError("The flow export permissions were revoked")
            ancestor: FlowRun | None = run
            while ancestor:
                if ancestor.parent_invocation_id:
                    parent = session.get(Invocation, ancestor.parent_invocation_id)
                    if (
                        parent
                        and parent.instance_id == instance.id
                        and parent.export_id == reference.export_id
                    ):
                        raise ConflictError("A job cannot re-enter its submission operation")
                ancestor = session.get(FlowRun, ancestor.parent_id) if ancestor.parent_id else None
        elif node.kind == "agent":
            assert node.configuration_revision_id is not None
            configuration_access_for(session).validate_agent(
                session,
                actor=actor,
                revision_id=node.configuration_revision_id,
                admission=self.admission,
            )
        elif node.kind == "subflow":
            assert node.revision_id is not None
            trail = set() if seen is None else set(seen)
            if (
                node.revision_id in trail
                or len(trail) + run.depth >= FlowLimits.model_validate(run.limits).max_depth
            ):
                raise InvalidArgumentError("The child definition exceeds its ancestor/depth bound")
            trail.add(node.revision_id)
            revision, definition = load_revision(
                session, actor=actor, revision_id=node.revision_id, use=True
            )
            if definition.kind != "flow":
                raise ForbiddenError("A subflow must reference a flow definition")
            for child in FlowDefinitionSpec.model_validate(revision.spec).graph.nodes:
                self.validate_node(session, actor=actor, run=run, node=child, seen=trail)
        if node.body:
            for child in node.body.nodes:
                self.validate_node(session, actor=actor, run=run, node=child, seen=seen)

    def validate_start(self, session: Session, *, actor: CurrentUser, run: FlowRun) -> None:
        load_flow_run(session, actor=actor, run_id=run.id, write=True)
        actor = self.actor(session, run)
        for node in FlowGraph.model_validate(
            self.cipher.decrypt_payload(run.graph_ciphertext)
        ).nodes:
            self.validate_node(session, actor=actor, run=run, node=node)

    def validate_definition(
        self,
        session: Session,
        actor: CurrentUser,
        spec: FlowDefinitionSpec,
    ) -> None:
        context = FlowRun(
            principal_id=actor.user_id,
            configuration_id=None,
            initiated_by_id=actor.user_id,
            depth=0,
            limits=spec.limits.model_dump(mode="json"),
        )
        for node in spec.graph.nodes:
            self.validate_node(session, actor=actor, run=context, node=node)

    def validate_subscription(
        self, session: Session, *, actor: CurrentUser, instance_id: UUID, revision_id: UUID
    ) -> None:
        instance = authorize_instance(session, instance_id, user=actor, scopes=self.scopes)
        load_revision(session, actor=actor, revision_id=revision_id, use=True)
        if instance.owner_id != actor.user_id:
            raise ForbiddenError("The event instance belongs to another execution principal")

    def validate_plan_source(
        self,
        session: Session,
        *,
        actor: CurrentUser,
        run: FlowRun,
        proposal: FlowPlanProposal,
        region: FlowNode,
        require_approval: bool = True,
    ) -> None:
        source = session.get(Invocation, proposal.source_invocation_id)
        if (
            source is None
            or source.principal_id != actor.user_id
            or source.status != "completed"
            or source.snapshot.get("configuration_id")
            != (str(run.configuration_id) if run.configuration_id else None)
        ):
            raise ForbiddenError(
                "The plan requires a completed, currently authorized source invocation"
            )
        instance = authorize_instance(session, source.instance_id, user=actor, scopes=self.scopes)
        installation = load_installation(session, source.installation_id)
        authorize_installation(session, installation, user=actor)
        permissions = source.snapshot.get("permissions", [])
        if (
            instance.status != "enabled"
            or installation.status != "enabled"
            or instance.revision != source.instance_revision
            or instance.installation_id != installation.id
            or source.snapshot.get("artifact") != installation.artifact
            or not isinstance(permissions, list)
            or not set(permissions)
            <= (set(instance.permissions) & set(installation.permission_ceiling))
        ):
            raise ForbiddenError("The plan source authorization was revoked")
        if not source.result_ciphertext:
            raise InvalidArgumentError("The source invocation has no recorded plan result")
        recorded = self.cipher.decrypt_payload(source.result_ciphertext)
        value = recorded.get("value", recorded)
        graph = value.get("graph") if isinstance(value, dict) else None
        if graph != proposal.graph.model_dump(mode="json"):
            raise ForbiddenError("The plan differs from its source effect receipt")
        for node in proposal.graph.nodes:
            self.validate_node(session, actor=actor, run=run, node=node)
        if region.approval_required and require_approval:
            approval = (
                approvals_for(session).get(session, proposal.approval_id)
                if proposal.approval_id
                else None
            )
            digest = payload_digest(proposal.graph.model_dump(mode="json"))
            if (
                approval is None
                or approval.requester_user_id != run.principal_id
                or approval.target_type != "flow_plan"
                or approval.target_id
                != f"{run.id}:{payload_digest({'path': proposal.step_path})}:{digest}"
                or effective_status(approval) != "approved"
            ):
                raise ForbiddenError("The proposed plan has no effective platform approval")

    def request_plan_review(
        self, session: Session, *, actor: CurrentUser, run_id: UUID, request: FlowPlanReview
    ) -> ApprovalSnapshot:
        if request.approval_id is not None:
            raise InvalidArgumentError("A review request cannot supply its own approval")
        run = load_flow_run(session, actor=actor, run_id=run_id, write=True, lock=True)
        region = validate_plan_request(session, run=run, request=request, cipher=self.cipher)
        self.validate_plan_source(
            session, actor=actor, run=run, proposal=request, region=region, require_approval=False
        )
        approver = self.users.load_identity(session, request.approver_user_id or run.principal_id)
        if approver is None or approver.status != "active":
            raise ForbiddenError("The plan approver is unavailable")
        configuration_access_for(session).authorize_approver(
            session,
            approver=approver,
            owner_id=run.principal_id,
            configuration_id=run.configuration_id,
        )
        digest = payload_digest(request.graph.model_dump(mode="json"))
        return approvals_for(session).create(
            session,
            kind="flow_plan",
            target=ApprovalTarget(
                "flow_plan",
                f"{run.id}:{payload_digest({'path': request.step_path})}:{digest}",
                digest,
            ),
            title="Dynamic flow plan review",
            summary="Approve this fixed expansion graph",
            details={"flow_run_id": str(run.id), "step_path": request.step_path, "digest": digest},
            approver_user_id=approver.user_id,
            requester=actor,
            expires_at=run.deadline,
        )

    def controller(self, session: Session, run: FlowRun) -> CurrentUser:
        user = self.users.load_identity(session, run.control_actor_id or run.principal_id)
        if user is None or user.status != "active":
            raise ForbiddenError("The flow controller is no longer active")
        return user

    def locked_run(self, session: Session, run_id: UUID) -> FlowRun:
        prior = session.get(FlowRun, run_id)
        if prior is None:
            raise ConflictError("The flow disappeared")
        return load_flow_run(
            session,
            actor=self.controller(session, prior),
            run_id=run_id,
            write=True,
            lock=True,
            allow_archived=True,
        )

    @activity.defn(name="flows.load")
    async def load(self, run_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self.load_state, UUID(run_id))

    def load_state(self, run_id: UUID) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            run = session.get(FlowRun, run_id)
            if run is None:
                raise ConflictError("The flow disappeared")
            actor = self.controller(session, run)
            authorize_scope(
                session,
                actor=actor,
                owner_id=run.principal_id,
                configuration_id=run.configuration_id,
                allow_archived=True,
            )
            commands = list(
                session.scalars(
                    select(FlowCommand)
                    .where(FlowCommand.run_id == run.id)
                    .order_by(FlowCommand.sequence)
                )
            )
            revision = session.get(DefinitionRevision, run.revision_id) if run.revision_id else None
            policy = (
                FlowDefinitionSpec.model_validate(revision.spec).policy
                if revision is not None
                else FlowExecutionPolicy()
            )
            return {
                "run_id": str(run.id),
                "status": run.status,
                "revision": run.revision,
                "graph": self.cipher.decrypt_payload(run.graph_ciphertext),
                "input": self.cipher.decrypt_payload(run.input_ciphertext),
                "deadline": run.deadline.isoformat(),
                "limits": run.limits,
                "policy": policy.model_dump(mode="json"),
                "waiting": run.active_steps == 0
                and session.scalar(
                    select(StepAttempt.id)
                    .where(StepAttempt.run_id == run.id, StepAttempt.status == "waiting")
                    .limit(1)
                )
                is not None,
                "commands": [
                    {"sequence": command.sequence, "kind": command.kind} for command in commands
                ],
            }

    @activity.defn(name="flows.project")
    async def project(self, projection: FlowProjection) -> dict[str, Any]:
        return await asyncio.to_thread(self.project_state, projection)

    def project_state(self, projection: FlowProjection) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            run = session.get(FlowRun, projection.run_id, with_for_update=True)
            if run is None:
                raise ConflictError("The flow disappeared")
            if projection.driver_generation != run.workflow_generation:
                return {"applied": True, "revision": run.revision, "status": run.status}
            fingerprint = payload_digest(projection.model_dump(mode="json"))
            previous = session.scalar(
                select(FlowProjectionReceipt).where(
                    FlowProjectionReceipt.run_id == run.id,
                    FlowProjectionReceipt.projection_id == projection.projection_id,
                )
            )
            if previous:
                if previous.fingerprint != fingerprint:
                    raise ConflictError("The flow projection occurrence changed")
                return {"applied": True, "revision": run.revision, "status": run.status}
            if run.revision != projection.expected_revision:
                return {"applied": False, "revision": run.revision, "status": run.status}
            if run.status in TERMINAL_FLOW_STATUSES:
                return {"applied": True, "revision": run.revision, "status": run.status}
            if projection.status == "succeeded" and run.revision_id:
                revision = session.get(DefinitionRevision, run.revision_id)
                assert revision is not None
                schema = FlowDefinitionSpec.model_validate(revision.spec).output_schema
                if list(Draft202012Validator(schema).iter_errors(projection.output)):
                    raise InvalidArgumentError("The flow output violates its published schema")
            run.status, run.error_code = projection.status, projection.error_code
            if projection.output is not None:
                run.output_ciphertext = self.cipher.encrypt_payload(
                    {"value": projection.output}
                ).ciphertext
            append_flow_event(
                session, run, projection.status, {"error_code": projection.error_code}
            )
            session.add(
                FlowProjectionReceipt(
                    run_id=run.id,
                    projection_id=projection.projection_id,
                    fingerprint=fingerprint,
                    revision=run.revision,
                )
            )
            return {"applied": True, "revision": run.revision, "status": run.status}

    @activity.defn(name="flows.begin_step")
    async def begin_step(self, command: StepCommand) -> dict[str, Any]:
        return await asyncio.to_thread(self.start_step, command)

    def start_step(self, command: StepCommand) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            prior = session.get(FlowRun, command.run_id)
            if prior is None:
                raise ConflictError("The flow disappeared")
            run = load_flow_run(
                session, actor=self.actor(session, prior), run_id=prior.id, write=True, lock=True
            )
            root = session.get(FlowRun, prior.root_id)
            assert root is not None and run is not None
            if command.driver_generation != run.workflow_generation:
                raise ConflictError("The flow driver generation was superseded")
            for cancellation in session.scalars(
                select(FlowCommand).where(
                    FlowCommand.run_id == run.id, FlowCommand.kind == "cancel_steps"
                )
            ):
                boundary = self.cipher.decrypt_payload(cancellation.payload_ciphertext)
                prefix = boundary["prefix"]
                if not isinstance(prefix, str):
                    raise ConflictError("The recorded cancellation boundary is invalid")
                if boundary["driver_generation"] == command.driver_generation and (
                    not prefix or command.path == prefix or command.path.startswith(prefix + "/")
                ):
                    return {"status": "cancelled"}
            actor = self.actor(session, run)
            authorize_scope(
                session,
                actor=actor,
                owner_id=run.principal_id,
                configuration_id=run.configuration_id,
                write=True,
            )
            node = node_at_path(session, run, command.path, self.cipher)
            if node != command.node or command.generation != 1:
                raise ForbiddenError("The step differs from its fixed graph contract")
            fingerprint = payload_digest(
                command.model_dump(mode="json", exclude={"driver_generation"})
            )
            previous = session.scalar(
                select(StepAttempt).where(
                    StepAttempt.run_id == run.id,
                    StepAttempt.path == command.path,
                    StepAttempt.generation == command.generation,
                )
            )
            if previous:
                if previous.fingerprint != fingerprint:
                    raise ConflictError("The step occurrence was reused with different input")
                return self.step_view(previous)
            if run.status in TERMINAL_FLOW_STATUSES or datetime.now(UTC) >= run.deadline:
                raise ConflictError("The flow is terminal or its deadline passed")
            latest = session.scalar(
                select(FlowCommand.kind)
                .where(
                    FlowCommand.run_id == run.id,
                    FlowCommand.kind.in_(("pause", "resume", "cancel")),
                )
                .order_by(FlowCommand.sequence.desc())
                .limit(1)
            )
            if latest in {"pause", "cancel"}:
                return {"status": "paused" if latest == "pause" else "cancelling"}
            limits = FlowLimits.model_validate(root.limits)
            leaf = not command.skip and node.kind in {"agent", "operation", "subflow"}
            if root.started_steps >= limits.max_steps:
                raise ConflictError("The shared flow step budget is exhausted")
            if leaf and node.kind != "subflow" and root.active_steps >= limits.max_concurrency:
                return {"status": "capacity_wait"}
            self.validate_node(session, actor=actor, run=run, node=node)
            if list(Draft202012Validator(node.input_schema).iter_errors(command.arguments)):
                raise InvalidArgumentError("The effective step input violates its schema")
            step = StepAttempt(
                run_id=run.id,
                path=command.path,
                generation=command.generation,
                fingerprint=fingerprint,
                node_ciphertext=self.cipher.encrypt_payload(
                    node.model_dump(mode="json")
                ).ciphertext,
                input_ciphertext=self.cipher.encrypt_payload(command.arguments).ciphertext,
            )
            session.add(step)
            root.started_steps += 1
            if leaf and node.kind != "subflow":
                root.active_steps += 1
            session.flush()
            if command.skip:
                step.status = "completed"
                append_flow_event(session, run, "step.skipped", {"path": step.path})
                return self.step_view(step)
            if node.kind == "operation":
                assert node.export is not None
                invocation = admit_invocation(
                    session,
                    user=actor,
                    instance_id=node.export.instance_id,
                    scopes=self.scopes,
                    cipher=self.cipher,
                    request=InvokeRequest(
                        export_id=node.export.export_id,
                        operation_id=f"flow:{step.id}",
                        arguments=command.arguments,
                    ),
                )
                invocation.deadline = min(invocation.deadline, run.deadline)
                step.child_kind, step.child_id = "invocation", invocation.id
            elif node.kind == "agent":
                assert node.configuration_revision_id is not None
                effective = command.arguments.get("input", command.arguments)
                agent_id = configuration_access_for(session).submit_agent(
                    session,
                    actor=actor,
                    revision_id=node.configuration_revision_id,
                    input=effective
                    if isinstance(effective, (str, list))
                    else json.dumps(effective),
                    initiated_by_id=run.initiated_by_id,
                    idempotency_key=f"flow:{step.id}",
                    admission=self.admission,
                    cipher=self.cipher,
                    assets=self.assets,
                )
                step.child_kind, step.child_id = "agent", agent_id
            elif node.kind == "subflow":
                assert node.revision_id is not None
                child = start_run(
                    session,
                    actor=actor,
                    request=FlowRunCreate(revision_id=node.revision_id, input=command.arguments),
                    idempotency_key=f"flow:{step.id}",
                    cipher=self.cipher,
                    parent=run,
                )
                step.child_kind, step.child_id = "flow", child.id
            elif node.kind == "expand":
                step.status = "waiting"
            elif node.kind == "wait":
                step.status = "waiting"
                step.wait_deadline = (
                    min(datetime.now(UTC) + timedelta(seconds=node.wait_seconds), run.deadline)
                    if node.wait_seconds or node.wait_kind == "timer"
                    else run.deadline
                )
                if node.wait_kind == "approval":
                    if node.approval_id:
                        approval = approvals_for(session).get(session, node.approval_id)
                        if (
                            approval is None
                            or approval.requester_user_id != run.principal_id
                            or approval.target_type != "flow_step"
                            or approval.target_id
                            != f"{run.id}:{payload_digest({'path': step.path})}"
                        ):
                            raise ForbiddenError("The approval does not own this flow occurrence")
                    else:
                        approver = self.users.load_identity(
                            session, node.approver_user_id or run.principal_id
                        )
                        if approver is None or approver.status != "active":
                            raise ForbiddenError("The assigned flow approver is unavailable")
                        configuration_access_for(session).authorize_approver(
                            session,
                            approver=approver,
                            owner_id=run.principal_id,
                            configuration_id=run.configuration_id,
                        )
                        approval = approvals_for(session).create(
                            session,
                            kind="flow_step",
                            target=ApprovalTarget(
                                "flow_step", f"{run.id}:{payload_digest({'path': step.path})}", "1"
                            ),
                            title="Flow step approval",
                            summary="Approve this fixed flow occurrence",
                            details={"flow_run_id": str(run.id), "step_path": step.path},
                            approver_user_id=approver.user_id,
                            requester=actor,
                            expires_at=step.wait_deadline,
                        )
                    step.child_kind, step.child_id = "approval", approval.id
            append_flow_event(session, run, "step.started", {"path": step.path, "kind": node.kind})
            return self.step_view(step)

    def step_view(self, step: StepAttempt) -> dict[str, Any]:
        return {
            "id": str(step.id),
            "status": step.status,
            "child_kind": step.child_kind,
            "child_id": str(step.child_id) if step.child_id else None,
            "result": self.cipher.decrypt_payload(step.result_ciphertext).get("value")
            if step.result_ciphertext
            else None,
            "error_code": step.error_code,
            "wait_deadline": step.wait_deadline.isoformat() if step.wait_deadline else None,
        }

    def finish_step(
        self,
        session: Session,
        *,
        run: FlowRun,
        step: StepAttempt,
        status: str,
        value: Any = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        node = FlowNode.model_validate(self.cipher.decrypt_payload(step.node_ciphertext))
        if status == "completed" and list(
            Draft202012Validator(node.output_schema).iter_errors(value)
        ):
            status, error_code, value = "needs_attention", "step_output_invalid", None
        if step.status not in {"completed", "failed", "cancelled"}:
            if status != "needs_attention" and step.child_kind in {"agent", "invocation"}:
                root = session.get(FlowRun, run.root_id, with_for_update=True)
                assert root is not None
                root.active_steps -= 1
            step.status, step.error_code = status, error_code
            if value is not None:
                step.result_ciphertext = self.cipher.encrypt_payload({"value": value}).ciphertext
            append_flow_event(
                session, run, f"step.{status}", {"path": step.path, "error_code": error_code}
            )
        return self.step_view(step)

    @activity.defn(name="flows.probe_step")
    async def probe_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.probe,
            UUID(payload["run_id"]),
            payload["path"],
            payload.get("driver_generation", 1),
        )

    def probe(self, run_id: UUID, path: str, driver_generation: int = 1) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            run = self.locked_run(session, run_id)
            if run is None:
                raise ConflictError("The flow disappeared")
            if driver_generation != run.workflow_generation:
                raise ConflictError("The flow driver generation was superseded")
            actor = self.actor(session, run)
            authorize_scope(
                session,
                actor=actor,
                owner_id=run.principal_id,
                configuration_id=run.configuration_id,
                write=True,
            )
            step = session.scalar(
                select(StepAttempt).where(
                    StepAttempt.run_id == run.id,
                    StepAttempt.path == path,
                    StepAttempt.generation == 1,
                )
            )
            if step is None:
                raise ConflictError("The flow step does not exist")
            if step.status in {"completed", "failed", "cancelled"}:
                return self.step_view(step)
            node = FlowNode.model_validate(self.cipher.decrypt_payload(step.node_ciphertext))
            if step.child_kind == "invocation":
                child = session.get(Invocation, step.child_id)
                if child is None:
                    raise ConflictError("The child invocation disappeared")
                if child.status in {"completed", "failed", "cancelled", "needs_attention"}:
                    result = (
                        self.cipher.decrypt_payload(child.result_ciphertext)
                        if child.result_ciphertext
                        else {}
                    )
                    return self.finish_step(
                        session,
                        run=run,
                        step=step,
                        status=child.status,
                        value=result.get("value", result),
                        error_code=child.error_code,
                    )
            elif step.child_kind == "agent":
                agent = session.get(Run, step.child_id)
                if agent is None:
                    raise ConflictError("The child Agent Run disappeared")
                if agent.status in {
                    "succeeded",
                    "failed",
                    "timed_out",
                    "cancelled",
                    "needs_attention",
                }:
                    result = (
                        self.cipher.decrypt_payload(agent.result_ciphertext)
                        if agent.result_ciphertext
                        else {}
                    )
                    status = (
                        "completed"
                        if agent.status == "succeeded"
                        else "failed"
                        if agent.status == "timed_out"
                        else agent.status
                    )
                    return self.finish_step(
                        session,
                        run=run,
                        step=step,
                        status=status,
                        value=result.get("output"),
                        error_code=agent.error_code,
                    )
            elif step.child_kind == "flow":
                flow = session.get(FlowRun, step.child_id)
                if flow is None:
                    raise ConflictError("The child flow disappeared")
                if flow.status in TERMINAL_FLOW_STATUSES:
                    flow_result: Any = (
                        self.cipher.decrypt_payload(flow.output_ciphertext).get("value")
                        if flow.output_ciphertext
                        else None
                    )
                    return self.finish_step(
                        session,
                        run=run,
                        step=step,
                        status="completed" if flow.status == "succeeded" else flow.status,
                        value=flow_result,
                        error_code=flow.error_code,
                    )
            elif node.kind == "wait":
                if (
                    node.wait_kind != "timer"
                    and step.wait_deadline
                    and datetime.now(UTC) >= step.wait_deadline
                ):
                    return self.finish_step(
                        session, run=run, step=step, status="failed", error_code="step_wait_timeout"
                    )
                if (
                    node.wait_kind == "timer"
                    and step.wait_deadline
                    and datetime.now(UTC) >= step.wait_deadline
                ):
                    return self.finish_step(
                        session, run=run, step=step, status="completed", value={}
                    )
                if node.wait_kind == "signal":
                    signal = session.scalar(
                        select(FlowSignalReceipt).where(
                            FlowSignalReceipt.run_id == run.id,
                            FlowSignalReceipt.step_path == step.path,
                            FlowSignalReceipt.generation == step.generation,
                            FlowSignalReceipt.name == node.signal_name,
                        )
                    )
                    if signal:
                        signal.consumed = True
                        return self.finish_step(
                            session,
                            run=run,
                            step=step,
                            status="completed",
                            value=self.cipher.decrypt_payload(signal.value_ciphertext)["value"],
                        )
                elif node.wait_kind == "approval":
                    approval = approvals_for(session).get(session, step.child_id)
                    if (
                        approval is None
                        or approval.requester_user_id != run.principal_id
                        or approval.target_type != "flow_step"
                        or approval.target_id != f"{run.id}:{payload_digest({'path': step.path})}"
                    ):
                        raise ForbiddenError("The approval does not authorize this flow step")
                    status = effective_status(approval)
                    if status != "pending":
                        return self.finish_step(
                            session,
                            run=run,
                            step=step,
                            status="completed" if status == "approved" else "failed",
                            value={"decision": status},
                        )
                elif node.wait_kind == "external":
                    prefix = step.path.rpartition("/")[0]
                    owned_path = f"{prefix}/{node.external_step}" if prefix else node.external_step
                    owned = session.scalar(
                        select(StepAttempt).where(
                            StepAttempt.run_id == run.id,
                            StepAttempt.path == owned_path,
                            StepAttempt.generation == 1,
                        )
                    )
                    if owned and owned.status in {
                        "completed",
                        "failed",
                        "cancelled",
                        "needs_attention",
                    }:
                        value = (
                            self.cipher.decrypt_payload(owned.result_ciphertext).get("value")
                            if owned.result_ciphertext
                            else None
                        )
                        return self.finish_step(
                            session, run=run, step=step, status=owned.status, value=value
                        )
            elif node.kind == "expand":
                plan = session.scalar(
                    select(FlowPlanApplication).where(
                        FlowPlanApplication.run_id == run.id,
                        FlowPlanApplication.step_path == step.path,
                    )
                )
                if plan:
                    return {
                        **self.step_view(step),
                        "graph": self.cipher.decrypt_payload(plan.graph_ciphertext),
                    }
            return self.step_view(step)

    @activity.defn(name="flows.finish_step")
    async def complete_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.complete_local_step, payload)

    def complete_local_step(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            run = self.locked_run(session, UUID(payload["run_id"]))
            if run is None:
                raise ConflictError("The flow disappeared")
            if payload.get("driver_generation", 1) != run.workflow_generation:
                raise ConflictError("The flow driver generation was superseded")
            actor = self.actor(session, run)
            authorize_scope(
                session,
                actor=actor,
                owner_id=run.principal_id,
                configuration_id=run.configuration_id,
                write=True,
            )
            step = session.scalar(
                select(StepAttempt).where(
                    StepAttempt.run_id == run.id,
                    StepAttempt.path == payload["path"],
                    StepAttempt.generation == 1,
                )
            )
            if step is None or step.child_kind:
                raise ConflictError("Only a local control node can be completed here")
            return self.finish_step(
                session, run=run, step=step, status=payload["status"], value=payload.get("value")
            )

    @activity.defn(name="flows.cancel_steps")
    async def cancel_steps(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.cancel_children,
            UUID(payload["run_id"]),
            payload.get("prefix", ""),
            payload.get("driver_generation"),
        )

    def authorize_invocation_cancellation(
        self, session: Session, invocation: Invocation, actor: CurrentUser
    ) -> None:
        step = session.scalar(
            select(StepAttempt).where(
                StepAttempt.child_kind == "invocation", StepAttempt.child_id == invocation.id
            )
        )
        if step is None:
            raise ForbiddenError("The flow does not own this invocation")
        flow = load_flow_run(
            session, actor=actor, run_id=step.run_id, write=True, allow_archived=True
        )
        if invocation.principal_id != flow.principal_id or (
            invocation.snapshot.get("configuration_id")
            != (str(flow.configuration_id) if flow.configuration_id else None)
        ):
            raise ForbiddenError("The invocation differs from its owning flow scope")

    def cancel_children(
        self, run_id: UUID, prefix: str = "", driver_generation: int | None = None
    ) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            run = self.locked_run(session, run_id)
            if run is None:
                raise ConflictError("The flow disappeared")
            actor = self.controller(session, run)
            generation = driver_generation or run.workflow_generation
            if generation != run.workflow_generation:
                raise ConflictError("The flow cancellation generation was superseded")
            if run.status not in TERMINAL_FLOW_STATUSES:
                record_command(
                    session,
                    run=run,
                    command_id=uuid5(
                        NAMESPACE_URL, f"flow-cancel-steps:{run.id}:{generation}:{prefix}"
                    ),
                    kind="cancel_steps",
                    payload={"prefix": prefix, "driver_generation": generation},
                    expected_revision=None,
                    cipher=self.cipher,
                )
                session.flush()
            steps = list(
                session.scalars(
                    select(StepAttempt).where(
                        StepAttempt.run_id == run.id,
                        or_(
                            StepAttempt.path == prefix,
                            StepAttempt.path.startswith(prefix.rstrip("/") + "/", autoescape=True),
                        )
                        if prefix
                        else true(),
                        StepAttempt.status.not_in(("completed", "failed", "cancelled")),
                    )
                )
            )
            pending, uncertain = 0, 0
            for step in steps:
                if step.child_kind == "agent":
                    child = session.get(Run, step.child_id)
                    if child is None or child.status == "needs_attention":
                        uncertain += 1
                    elif child and child.status not in {
                        "succeeded",
                        "failed",
                        "cancelled",
                        "timed_out",
                    }:
                        cancel_agent(
                            session,
                            subject=ExecutionSubject(child.principal_id),
                            admission=OwnedAgentCancellation(run.id, step.id, actor),
                            run_id=child.id,
                            cipher=self.cipher,
                            request=CancelRequest(
                                command_id=uuid5(NAMESPACE_URL, f"flow-cancel:{step.id}"),
                                expected_revision=child.event_sequence,
                            ),
                        )
                        pending += 1
                    else:
                        self.finish_step(session, run=run, step=step, status="cancelled")
                elif step.child_kind == "invocation":
                    invocation = session.get(Invocation, step.child_id)
                    if invocation is None or invocation.status == "needs_attention":
                        uncertain += 1
                    elif invocation and invocation.status in {"queued", "started"}:
                        cancel_invocation(
                            session,
                            invocation=invocation,
                            user=actor,
                            expected_revision=invocation.revision,
                            authorize_owner=self.authorize_invocation_cancellation,
                        )
                        pending += 1
                    else:
                        self.finish_step(session, run=run, step=step, status="cancelled")
                elif step.child_kind == "flow":
                    child_flow = session.get(FlowRun, step.child_id)
                    if child_flow is None or child_flow.status == "needs_attention":
                        uncertain += 1
                    elif child_flow and child_flow.status not in TERMINAL_FLOW_STATUSES:
                        control_run(
                            session,
                            actor=actor,
                            run_id=child_flow.id,
                            cipher=self.cipher,
                            request=FlowControl(
                                command_id=uuid5(NAMESPACE_URL, f"flow-cancel:{step.id}"),
                                expected_revision=child_flow.revision,
                                action="cancel",
                            ),
                            allow_archived=True,
                        )
                        pending += 1
                    else:
                        self.finish_step(session, run=run, step=step, status="cancelled")
                elif step.child_kind == "approval":
                    approval = approvals_for(session).get(session, step.child_id)
                    if approval is None:
                        uncertain += 1
                    else:
                        if effective_status(approval) == "pending":
                            approvals_for(session).cancel(
                                session,
                                approval_id=approval.id,
                                actor=actor,
                                reason="The owning flow cancelled this wait",
                            )
                        self.finish_step(session, run=run, step=step, status="cancelled")
                else:
                    self.finish_step(session, run=run, step=step, status="cancelled")
            return {"pending": pending, "uncertain": uncertain}


class RunActivities:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        admission: ScopeAdmission,
        bindings: ExecutionBindings,
        cipher: SecretCipher,
    ) -> None:
        self.sessions = sessions
        self.admission = admission
        self.bindings = bindings
        self.cipher = cipher

    @activity.defn(name="orchestration.prepare")
    async def prepare(self, reference: RunReference) -> RunClaim:
        return RunClaim(prepared=await asyncio.to_thread(self._prepare, reference))

    def _prepare(self, reference: RunReference) -> PreparedRun | None:
        try:
            with self.sessions() as session, session.begin():
                if self.bindings.admission_open is not None and not self.bindings.admission_open(
                    session
                ):
                    return None
                prior = session.get(Run, reference.run_id)
                if prior is None:
                    raise ConflictError("The execution Run does not exist")
                run = load_run(
                    session,
                    run_id=prior.id,
                    subject=ExecutionSubject(prior.principal_id),
                    admission=self.admission,
                    lock=True,
                )
                if run.status == "cancelling":
                    return None
                if run.status not in {"queued", "preparing", "running"}:
                    raise ConflictError("The Run is not claimable")
                if not is_session_head(session, run):
                    return None
                model_session = session.get(SessionRecord, run.session_id)
                if model_session is None or model_session.status not in {"open", "closing"}:
                    raise ConflictError("The model Session is blocked or closed")
                request = ExecutionRequest.model_validate(
                    self.cipher.decrypt_payload(run.request_ciphertext)
                )
                for agent in request.agents.values():
                    for tool in agent.tools:
                        enforce_operation_policy(
                            session,
                            action="tool.invoke",
                            name=tool.name,
                            approval_configured=tool.approval_required,
                        )
                if (
                    request.configuration_source is not None
                    or request.parent_invocation_id is not None
                ):
                    if self.bindings.sources is None:
                        raise ConflictError("Configuration source authorization is not assembled")
                    self.bindings.sources.authorize(session, run_id=run.id)
                has_extensions = request.hooks or any(
                    tool.extension for agent in request.agents.values() for tool in agent.tools
                )
                if has_extensions:
                    if self.bindings.extensions is None:
                        raise ConflictError("Installed execution bindings are not assembled")
                    self.bindings.extensions.validate(
                        session,
                        principal_id=run.principal_id,
                        scope=record_scope(model_session, principal_id=run.principal_id),
                        configuration=request.model_dump(mode="json"),
                    )
                acquire_capacity(session, run, self.bindings)
                prepare_run(session, model_session=model_session, run_id=run.id)
                run.status = "running"
                run.started_at = run.started_at or datetime.now(UTC)
                append_event(
                    session,
                    run=run,
                    cipher=self.cipher,
                    event_key=f"{run.id}:running:{run.capacity_epoch}",
                    kind="running",
                    payload={"status": "running"},
                )
                return PreparedRun(
                    reference=reference,
                    principal_id=run.principal_id,
                    request=ExecutionRequest.model_validate(
                        self.cipher.decrypt_payload(run.request_ciphertext)
                    ),
                )
        except DomainError as exc:
            if exc.code == "capacity_exceeded":
                return None
            raise

    @activity.defn(name="orchestration.finish")
    async def finish(self, completion: RunCompletion) -> str:
        return await asyncio.to_thread(self._finish, completion)

    def _finish(self, completion: RunCompletion) -> str:
        with self.sessions() as session, session.begin():
            run = lock_run_facts(session, completion.run_id)
            if run.status in {"succeeded", "failed", "cancelled", "timed_out", "needs_attention"}:
                return run.status
            unresolved = list(
                session.scalars(
                    select(OperationReceipt)
                    .where(
                        OperationReceipt.run_id == run.id,
                        OperationReceipt.status.in_(("started", "uncertain")),
                    )
                    .order_by(OperationReceipt.operation_id)
                )
            )
            runtime_pending = session.scalar(
                select(EnvironmentOperationReceipt.operation_id)
                .where(
                    EnvironmentOperationReceipt.run_id == run.id,
                    EnvironmentOperationReceipt.status.in_(("pending", "started", "uncertain")),
                )
                .limit(1)
            )
            # Unknown external effects may still be running. Only reconciliation
            # can release their slot after confirming the effect has ended.
            if not unresolved and runtime_pending is None:
                release_capacity(session, run, self.bindings)
            for receipt in unresolved:
                retain_uncertain(session, run=run, receipt=receipt)
            run.status = "needs_attention" if unresolved or runtime_pending else completion.status
            run.error_code = completion.error_code
            if run.status == "succeeded":
                model_session = session.get(SessionRecord, run.session_id)
                if model_session is None:
                    raise ConflictError("The model Session does not exist")
                commit_run(session, model_session=model_session, run_id=run.id)
                run.result_ciphertext = self.cipher.encrypt_payload(
                    {
                        "output": completion.output,
                        "artifacts": [str(item) for item in completion.artifacts],
                    }
                ).ciphertext
            run.completed_at = datetime.now(UTC)
            model_session = session.get(SessionRecord, run.session_id)
            if model_session is not None and model_session.status == "closing":
                remaining = session.scalar(
                    select(Run.id)
                    .where(
                        Run.session_id == run.session_id,
                        Run.id != run.id,
                        Run.status.in_(RUN_ACTIVE_STATUSES),
                    )
                    .limit(1)
                )
                if remaining is None and run.status != "needs_attention":
                    model_session.status = "closed"
            append_event(
                session,
                run=run,
                cipher=self.cipher,
                event_key=f"{run.id}:finished",
                kind=run.status,
                payload={"status": run.status, "error_code": run.error_code},
            )
            return run.status

    @activity.defn(name="orchestration.checkpoint")
    async def checkpoint(self, checkpoint: ApprovalCheckpoint) -> UUID:
        return await asyncio.to_thread(self._checkpoint, checkpoint)

    def _checkpoint(self, checkpoint: ApprovalCheckpoint) -> UUID:
        with self.sessions() as session, session.begin():
            run = lock_run_facts(session, checkpoint.run_id)
            previous = session.scalar(
                select(RunApproval).where(
                    RunApproval.run_id == run.id,
                    RunApproval.checkpoint_key == checkpoint.checkpoint_key,
                )
            )
            if previous is not None and previous.approval_id is not None:
                return previous.approval_id
            if run.status != "running":
                raise ConflictError("The Run cannot enter an approval wait")
            request = ExecutionRequest.model_validate(
                self.cipher.decrypt_payload(run.request_ciphertext)
            )
            approval = approvals_for(session).create(
                session,
                kind="orchestration.tool",
                target=ApprovalTarget(
                    target_type="orchestration.run",
                    target_id=f"{run.id}:{checkpoint.checkpoint_key}",
                    target_version=checkpoint.checkpoint_key,
                ),
                title="Execution tool approval",
                summary="Approve the frozen tool invocations",
                details={"run_id": str(run.id)},
                approver_user_id=run.principal_id,
                requester=ExecutionSubject(run.principal_id),
                expires_at=datetime.now(UTC)
                + timedelta(seconds=request.limits.approval_timeout_seconds),
            )
            session.add(
                RunApproval(
                    scope_id=run.scope_id,
                    run_id=run.id,
                    checkpoint_key=checkpoint.checkpoint_key,
                    approval_id=approval.id,
                    state_ciphertext=self.cipher.encrypt_payload(
                        {"state": checkpoint.state}
                    ).ciphertext,
                    interruptions_ciphertext=self.cipher.encrypt_payload(
                        {"interruptions": checkpoint.interruptions}
                    ).ciphertext,
                )
            )
            run.status = "waiting_approval"
            release_capacity(session, run, self.bindings)
            append_event(
                session,
                run=run,
                cipher=self.cipher,
                event_key=f"{run.id}:approval:{checkpoint.checkpoint_key}",
                kind="waiting_approval",
                payload={"approval_id": str(approval.id)},
            )
            return approval.id

    @activity.defn(name="orchestration.approval")
    async def apply_approval(self, run_id: UUID, approval_id: UUID) -> dict[str, Any]:
        return await asyncio.to_thread(self._apply_approval, run_id, approval_id)

    def _apply_approval(self, run_id: UUID, approval_id: UUID) -> dict[str, Any]:
        with self.sessions() as session, session.begin():
            run = lock_run_facts(session, run_id)
            checkpoint = session.scalar(
                select(RunApproval).where(
                    RunApproval.run_id == run.id, RunApproval.approval_id == approval_id
                )
            )
            decision = approvals_for(session).get(session, approval_id)
            if (
                checkpoint is None
                or decision is None
                or decision.status not in {"approved", "rejected"}
            ):
                raise ConflictError("No effective platform approval decision exists")
            if run.status == "cancelling":
                raise ConflictError("The Run was cancelled")
            if checkpoint.approved is not None:
                return {"approved": checkpoint.approved}
            if run.status != "waiting_approval":
                raise ConflictError("The Run is no longer waiting for this approval")
            request = ExecutionRequest.model_validate(
                self.cipher.decrypt_payload(run.request_ciphertext)
            )
            if request.configuration_source is not None or request.parent_invocation_id is not None:
                if self.bindings.sources is None:
                    raise ConflictError("Configuration source authorization is not assembled")
                self.bindings.sources.authorize(session, run_id=run.id)
            checkpoint.approved = decision.status == "approved"
            checkpoint.decided_by = decision.decided_by_user_id
            checkpoint.decided_at = decision.decided_at
            run.status = "queued"
            return {"approved": checkpoint.approved}
