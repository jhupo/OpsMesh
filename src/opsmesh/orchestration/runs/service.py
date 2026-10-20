"""Composition-owned state and explicit execution operations."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.contracts import (
    ExecutionSubject,
    ScopeAdmission,
    configuration_access_for,
)
from opsmesh.orchestration.definitions.contracts import (
    TERMINAL_FLOW_STATUSES,
    FlowDefinitionSpec,
    FlowGraph,
    FlowLimits,
    FlowReference,
    FlowSignal,
)
from opsmesh.orchestration.definitions.graph import validate_graph
from opsmesh.orchestration.definitions.service import load_revision
from opsmesh.orchestration.execution.contracts import ExecutionRequest, RunReference
from opsmesh.orchestration.runs.access import load_flow_run, load_run
from opsmesh.orchestration.runs.events import append_event, append_flow_event
from opsmesh.orchestration.runs.models import (
    FlowCommand,
    FlowPlanApplication,
    FlowRun,
    FlowSignalReceipt,
    Run,
    StepAttempt,
)
from opsmesh.orchestration.runs.schemas import (
    CancelRequest,
    FlowControl,
    FlowPlanProposal,
    FlowRunCreate,
    FlowRunView,
    RunView,
)
from opsmesh.orchestration.sessions.access import load_visible_session
from opsmesh.shared.db.idempotency import run_idempotent_create
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError
from opsmesh.shared.security.encryption import SecretCipher
from opsmesh.shared.serialization import canonical_json, payload_digest
from opsmesh.temporal.dispatch.outbox import stage_execution, stage_signal


def start_run(
    session: Session,
    *,
    actor: CurrentUser,
    request: FlowRunCreate,
    idempotency_key: str,
    cipher: SecretCipher,
    parent: FlowRun | None = None,
    parent_invocation_id: UUID | None = None,
) -> FlowRun:
    configuration_id = parent.configuration_id if parent else request.configuration_id
    definition_actor, fixed_revision = (
        configuration_access_for(session).flow_definition(
            session, actor=actor, configuration_id=configuration_id
        )
        if configuration_id
        else (actor, None)
    )
    principal = actor
    revision, definition = load_revision(
        session,
        actor=definition_actor,
        revision_id=request.revision_id,
        use=True,
    )
    if definition.kind != "flow":
        raise InvalidArgumentError("Agent graphs run directly through configuration execution")
    if configuration_id is not None and parent is None and fixed_revision != revision.id:
        raise ForbiddenError("The flow revision differs from the fixed use configuration")
    spec = FlowDefinitionSpec.model_validate(revision.spec)
    if len(canonical_json(request.input).encode()) > 262144 or list(
        Draft202012Validator(spec.input_schema).iter_errors(request.input)
    ):
        raise InvalidArgumentError("Flow input does not match the published contract")
    if parent is not None:
        if request.configuration_id not in {None, parent.configuration_id}:
            raise ForbiddenError("A child flow cannot change its parent's configuration")
        root = session.get(FlowRun, parent.root_id, with_for_update=True)
        if (
            root is None
            or root.status in {"failed", "cancelled", "needs_attention"}
            or datetime.now(UTC) >= root.deadline
        ):
            raise ConflictError("The ancestor flow is no longer active")
        limits = FlowLimits.model_validate(root.limits)
        if parent.depth + 1 >= limits.max_depth:
            raise ConflictError("The child flow exceeds the root depth limit")
        current: FlowRun | None = parent
        while current:
            if current.revision_id == revision.id:
                raise ConflictError("A flow cannot submit its own unfinished ancestor revision")
            current = session.get(FlowRun, current.parent_id) if current.parent_id else None

    def create() -> FlowRun:
        identifier = uuid4()
        deadline = datetime.now(UTC) + timedelta(seconds=spec.limits.timeout_seconds)
        if parent:
            deadline = min(deadline, parent.deadline)
        record = FlowRun(
            id=identifier,
            revision_id=revision.id,
            principal_id=principal.user_id,
            initiated_by_id=parent.initiated_by_id if parent else actor.user_id,
            configuration_id=configuration_id,
            root_id=parent.root_id if parent else identifier,
            parent_id=parent.id if parent else None,
            parent_invocation_id=parent_invocation_id,
            depth=parent.depth + 1 if parent else 0,
            deadline=deadline,
            limits=(parent.limits if parent else spec.limits.model_dump(mode="json")),
            graph_ciphertext=cipher.encrypt_payload(spec.graph.model_dump(mode="json")).ciphertext,
            input_ciphertext=cipher.encrypt_payload(request.input).ciphertext,
        )
        session.add(record)
        session.flush()
        append_flow_event(session, record, "queued", {"revision_id": str(revision.id)})
        stage_execution(
            session,
            FlowReference(
                execution_id=record.id, run_id=record.id, workflow_id=f"flow:{record.id}:1"
            ),
        )
        return record

    return run_idempotent_create(
        session=session,
        scope_id=configuration_id or actor.user_id,
        actor_id=actor.user_id,
        operation="orchestration.start",
        idempotency_key=idempotency_key,
        request_payload={
            **request.model_dump(mode="json"),
            "parent_id": str(parent.id) if parent else None,
            "parent_invocation_id": str(parent_invocation_id) if parent_invocation_id else None,
        },
        get_existing=lambda identifier: load_flow_run(session, actor=actor, run_id=identifier),
        create=create,
        resource_id=lambda record: record.id,
    )


def flow_run_view(run: FlowRun, cipher: SecretCipher) -> FlowRunView:
    return FlowRunView(
        id=run.id,
        revision_id=run.revision_id,
        configuration_id=run.configuration_id,
        initiated_by_id=run.initiated_by_id,
        parent_id=run.parent_id,
        root_id=run.root_id,
        status=run.status,
        revision=run.revision,
        output=cipher.decrypt_payload(run.output_ciphertext).get("value")
        if run.output_ciphertext
        else None,
        error_code=run.error_code,
    )


def record_command(
    session: Session,
    *,
    run: FlowRun,
    command_id: UUID,
    kind: str,
    payload: dict[str, Any],
    expected_revision: int | None,
    cipher: SecretCipher,
) -> FlowCommand:
    fingerprint = payload_digest({"kind": kind, "payload": payload})
    previous = session.scalar(
        select(FlowCommand).where(
            FlowCommand.run_id == run.id, FlowCommand.command_id == command_id
        )
    )
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ConflictError("The command identity was reused with different content")
        return previous
    if expected_revision is not None and run.revision != expected_revision:
        raise ConflictError("The flow revision changed")
    if run.status in TERMINAL_FLOW_STATUSES:
        raise ConflictError("The flow is terminal")
    run.command_sequence += 1
    command = FlowCommand(
        run_id=run.id,
        command_id=command_id,
        sequence=run.command_sequence,
        kind=kind,
        fingerprint=fingerprint,
        payload_ciphertext=cipher.encrypt_payload(payload).ciphertext,
    )
    session.add(command)
    append_flow_event(session, run, f"command.{kind}", {"command_id": str(command_id)})
    if kind != "retry":
        stage_signal(
            session,
            dispatch_id=uuid4(),
            workflow_id=f"flow:{run.id}:{run.workflow_generation}",
            signal_name="wake",
            signal_args=(),
        )
    return command


def control_run(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    request: FlowControl,
    cipher: SecretCipher,
    allow_archived: bool = False,
) -> FlowRun:
    run = load_flow_run(
        session, actor=actor, run_id=run_id, write=True, lock=True, allow_archived=allow_archived
    )
    if request.action == "retry":
        previous = session.scalar(
            select(FlowCommand).where(
                FlowCommand.run_id == run.id, FlowCommand.command_id == request.command_id
            )
        )
        if previous:
            if previous.fingerprint != payload_digest(
                {"kind": "retry", "payload": request.model_dump(mode="json")}
            ):
                raise ConflictError("The retry command identity was reused")
            return run
        if run.revision != request.expected_revision or run.status not in {
            "failed",
            "needs_attention",
        }:
            raise ConflictError("Retry requires the current failed or uncertain flow revision")
        if datetime.now(UTC) >= run.deadline:
            raise ConflictError("Retry cannot extend the root execution deadline")
        # Recovery retains attempts and child references; effects are never resubmitted.
        run.status, run.error_code = "queued", None
        run.workflow_generation += 1
        run.control_actor_id = actor.user_id
        record_command(
            session,
            run=run,
            command_id=request.command_id,
            kind="retry",
            payload=request.model_dump(mode="json"),
            expected_revision=request.expected_revision,
            cipher=cipher,
        )
        stage_execution(
            session,
            FlowReference(
                execution_id=uuid4(),
                run_id=run.id,
                workflow_id=f"flow:{run.id}:{run.workflow_generation}",
                generation=run.workflow_generation,
                command_cursor=run.command_sequence,
            ),
        )
        return run
    run.control_actor_id = actor.user_id
    record_command(
        session,
        run=run,
        command_id=request.command_id,
        kind=request.action,
        payload=request.model_dump(mode="json"),
        expected_revision=request.expected_revision,
        cipher=cipher,
    )
    return run


def node_at_path(session: Session, run: FlowRun, path: str, cipher: SecretCipher) -> Any:
    graph = FlowGraph.model_validate(cipher.decrypt_payload(run.graph_ciphertext))
    parts = path.split("/")
    prefix: list[str] = []
    while parts:
        key = parts.pop(0)
        node = next((item for item in graph.nodes if item.key == key), None)
        if node is None:
            raise InvalidArgumentError("The step path is not part of the fixed flow graph")
        prefix.append(key)
        if not parts:
            return node
        index = parts.pop(0)
        if not index.isdecimal() or int(index) >= (
            node.max_iterations if node.kind in {"foreach", "repeat"} else 1
        ):
            raise InvalidArgumentError("The child step index exceeds its frozen bound")
        if node.kind == "expand":
            application = session.scalar(
                select(FlowPlanApplication).where(
                    FlowPlanApplication.run_id == run.id,
                    FlowPlanApplication.step_path == "/".join(prefix),
                )
            )
            if application is None:
                raise ConflictError("The expansion has no accepted plan")
            graph = FlowGraph.model_validate(cipher.decrypt_payload(application.graph_ciphertext))
        elif node.body:
            graph = node.body
        else:
            raise InvalidArgumentError("The node has no child graph")
        prefix.append(index)
    raise InvalidArgumentError("The step path is empty")


def submit_signal(
    session: Session, *, actor: CurrentUser, run_id: UUID, request: FlowSignal, cipher: SecretCipher
) -> FlowRun:
    run = load_flow_run(session, actor=actor, run_id=run_id, write=True, lock=True)
    node = node_at_path(session, run, request.step_path, cipher)
    if (
        node.kind != "wait"
        or node.wait_kind != "signal"
        or node.signal_name != request.name
        or request.generation != 1
    ):
        raise InvalidArgumentError("The signal target, name or wait generation is invalid")
    step = session.scalar(
        select(StepAttempt).where(
            StepAttempt.run_id == run.id,
            StepAttempt.path == request.step_path,
            StepAttempt.generation == request.generation,
        )
    )
    payload = request.model_dump(mode="json")
    previous = session.scalar(
        select(FlowSignalReceipt).where(
            FlowSignalReceipt.run_id == run.id,
            FlowSignalReceipt.step_path == request.step_path,
            FlowSignalReceipt.generation == request.generation,
            FlowSignalReceipt.name == request.name,
        )
    )
    if previous:
        if previous.command_id != request.command_id or cipher.decrypt_payload(
            previous.value_ciphertext
        ) != {"value": request.value}:
            raise ConflictError("This signal generation already has a different receipt")
        return run
    if step is not None and step.status in {"completed", "cancelled", "failed"}:
        raise ConflictError("The signal wait generation already ended")
    record_command(
        session,
        run=run,
        command_id=request.command_id,
        kind="signal",
        payload=payload,
        expected_revision=None,
        cipher=cipher,
    )
    session.add(
        FlowSignalReceipt(
            run_id=run.id,
            step_path=request.step_path,
            generation=request.generation,
            name=request.name,
            command_id=request.command_id,
            value_ciphertext=cipher.encrypt_payload({"value": request.value}).ciphertext,
        )
    )
    session.flush()
    return run


def validate_plan_region(region: Any, graph: FlowGraph) -> None:
    if len(graph.nodes) > region.max_expanded_nodes:
        raise InvalidArgumentError("The plan exceeds the declared expansion bound")
    validate_graph(graph)
    for node in graph.nodes:
        if node.kind not in region.allowed_kinds:
            raise ForbiddenError("The proposed node kind is outside the expansion allowlist")
        reference = (
            str(node.revision_id)
            if node.revision_id
            else str(node.export.instance_id)
            if node.export
            else str(node.configuration_revision_id)
            if node.kind == "agent"
            else None
        )
        if reference is not None and reference not in region.allowed_references:
            raise ForbiddenError("The proposed target is outside the expansion allowlist")
        if node.body:
            validate_plan_region(region, node.body)


def accept_plan(
    session: Session,
    *,
    actor: CurrentUser,
    run_id: UUID,
    request: FlowPlanProposal,
    cipher: SecretCipher,
    validate_source: Any,
) -> FlowRun:
    run = load_flow_run(session, actor=actor, run_id=run_id, write=True, lock=True)
    previous = session.scalar(
        select(FlowCommand).where(
            FlowCommand.run_id == run.id, FlowCommand.command_id == request.command_id
        )
    )
    if previous:
        if previous.fingerprint != payload_digest(
            {"kind": "plan", "payload": request.model_dump(mode="json")}
        ):
            raise ConflictError("The plan command identity was reused")
        return run
    region = validate_plan_request(session, run=run, request=request, cipher=cipher)
    validate_source(session, actor=actor, run=run, proposal=request, region=region)
    record_command(
        session,
        run=run,
        command_id=request.command_id,
        kind="plan",
        payload=request.model_dump(mode="json"),
        expected_revision=request.expected_revision,
        cipher=cipher,
    )
    session.add(
        FlowPlanApplication(
            run_id=run.id,
            step_path=request.step_path,
            source_invocation_id=request.source_invocation_id,
            revision=run.revision,
            digest=payload_digest(request.graph.model_dump(mode="json")),
            graph_ciphertext=cipher.encrypt_payload(
                request.graph.model_dump(mode="json")
            ).ciphertext,
        )
    )
    return run


def validate_plan_request(
    session: Session, *, run: FlowRun, request: FlowPlanProposal, cipher: SecretCipher
) -> Any:
    if run.status in TERMINAL_FLOW_STATUSES or datetime.now(UTC) >= run.deadline:
        raise ConflictError("The flow is terminal or its deadline passed")
    if run.revision != request.expected_revision:
        raise ConflictError("The flow revision changed")
    region = node_at_path(session, run, request.step_path, cipher)
    if region.kind != "expand":
        raise ForbiddenError("The target is not a declared expansion region")
    started = session.scalar(
        select(StepAttempt.id)
        .where(
            StepAttempt.run_id == run.id,
            StepAttempt.path.startswith(request.step_path + "/", autoescape=True),
        )
        .limit(1)
    )
    applied = session.scalar(
        select(FlowPlanApplication.id).where(
            FlowPlanApplication.run_id == run.id, FlowPlanApplication.step_path == request.step_path
        )
    )
    if started is not None or applied is not None:
        raise ConflictError("The expansion region already has started nodes or an applied plan")
    validate_plan_region(region, request.graph)
    root = session.get(FlowRun, run.root_id, with_for_update=True)
    if (
        root is None
        or root.started_steps + validate_graph(request.graph)
        > FlowLimits.model_validate(root.limits).max_steps
    ):
        raise ConflictError("The plan exceeds the shared root step limit")
    return region


def execute(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    cipher: SecretCipher,
    request: ExecutionRequest,
    idempotency_key: str,
) -> Run:
    model_session = load_visible_session(
        session, session_id=request.session_id, subject=subject, admission=admission, lock=True
    )

    def existing(run_id: UUID) -> Run:
        return load_run(session, run_id=run_id, subject=subject, admission=admission)

    def create() -> Run:
        if model_session.status != "open":
            raise ConflictError("The model session does not accept new Runs")
        run = Run(
            scope_id=model_session.configuration_id or subject.user_id,
            principal_id=subject.user_id,
            initiated_by_id=subject.user_id,
            owner_ref=(
                f"configuration:{model_session.configuration_id}"
                if model_session.configuration_id
                else f"user:{subject.user_id}"
            ),
            session_id=model_session.id,
            sequence=model_session.next_sequence,
            status="queued",
            request_ciphertext=cipher.encrypt_payload(request.model_dump(mode="json")).ciphertext,
        )
        model_session.next_sequence += 1
        session.add(run)
        session.flush()
        append_event(
            session,
            run=run,
            cipher=cipher,
            event_key=f"{run.id}:submitted",
            kind="queued",
            payload={"status": "queued"},
        )
        stage_execution(
            session,
            RunReference(
                execution_id=run.id,
                run_id=run.id,
                workflow_id=f"run:{run.id}",
            ),
        )
        return run

    return run_idempotent_create(
        session=session,
        scope_id=model_session.scope_id,
        actor_id=subject.user_id,
        operation="orchestration.run",
        idempotency_key=idempotency_key,
        request_payload=request.model_dump(mode="json"),
        get_existing=existing,
        create=create,
        resource_id=lambda run: run.id,
    )


def cancel(
    session: Session,
    *,
    subject: ExecutionSubject,
    admission: ScopeAdmission,
    run_id: UUID,
    cipher: SecretCipher,
    request: CancelRequest,
) -> Run:
    run = load_run(session, run_id=run_id, subject=subject, admission=admission, lock=True)

    return run_idempotent_create(
        session=session,
        scope_id=run.scope_id,
        actor_id=subject.user_id,
        operation=f"execution.cancel:{run.id}",
        idempotency_key=str(request.command_id),
        request_payload=request.model_dump(mode="json"),
        get_existing=lambda _: run,
        create=lambda: _cancel(
            session, run=run, cipher=cipher, expected_revision=request.expected_revision
        ),
        resource_id=lambda item: item.id,
    )


def _cancel(session: Session, *, run: Run, cipher: SecretCipher, expected_revision: int) -> Run:
    if run.event_sequence != expected_revision:
        raise ConflictError("The Run revision changed")
    if run.status in {"succeeded", "failed", "cancelled", "timed_out", "needs_attention"}:
        return run
    run.status = "cancelling"
    append_event(
        session,
        run=run,
        cipher=cipher,
        event_key=f"{run.id}:cancel",
        kind="cancelling",
        payload={"status": run.status},
    )
    stage_signal(
        session,
        dispatch_id=uuid4(),
        workflow_id=f"run:{run.id}",
        signal_name="cancel",
        signal_args=(),
    )
    return run


def run_view(run: Run, cipher: SecretCipher) -> RunView:
    result = cipher.decrypt_payload(run.result_ciphertext) if run.result_ciphertext else {}
    return RunView(
        run_id=run.id,
        session_id=run.session_id,
        sequence=run.sequence,
        status=run.status,
        output=result.get("output"),
        artifacts=result.get("artifacts", []),
        usage=run.usage,
        error_code=run.error_code,
        revision=run.event_sequence,
        created_at=run.created_at,
    )
