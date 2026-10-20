"""Reauthorize immutable resource versions and transfer them through runtime receipts."""

import asyncio
import base64
from uuid import UUID, uuid5

from temporalio import activity

from opsmesh.orchestration.contracts import ExecutionSubject, RunAssets
from opsmesh.orchestration.execution.contracts import ExecutionRequest, RunReference
from opsmesh.orchestration.runs.access import load_run
from opsmesh.orchestration.runs.activities import RunActivities
from opsmesh.orchestration.runs.models import Run
from opsmesh.orchestration.runtime.access import load_environment
from opsmesh.orchestration.runtime.activities import RuntimeActivities
from opsmesh.orchestration.runtime.contracts import RuntimeCommand
from opsmesh.orchestration.runtime.models import EnvironmentOperationReceipt
from opsmesh.orchestration.runtime.service import stage_operation
from opsmesh.orchestration.sessions.access import record_scope
from opsmesh.orchestration.sessions.models import SessionRecord
from opsmesh.shared.errors import ConflictError, ForbiddenError
from opsmesh.shared.serialization import payload_digest


class AssetActivities:
    def __init__(self, runs: RunActivities, runtime: RuntimeActivities, assets: RunAssets) -> None:
        self.runs, self.runtime, self.assets = runs, runtime, assets

    def commands(self, reference: RunReference) -> list[RuntimeCommand]:
        r = self.runs
        with r.sessions() as session, session.begin():
            prior = session.get(Run, reference.run_id)
            if prior is None:
                raise ConflictError("The Run does not exist")
            run = load_run(
                session,
                run_id=prior.id,
                subject=ExecutionSubject(prior.principal_id),
                admission=r.admission,
                lock=True,
            )
            if run.status != "running":
                raise ConflictError("The Run may not transfer assets")
            frozen = ExecutionRequest.model_validate(
                r.cipher.decrypt_payload(run.request_ciphertext)
            )
            if not frozen.assets:
                return []
            if frozen.environment_id is None:
                raise ConflictError("Run assets require an explicit environment")
            return [
                RuntimeCommand(
                    execution_id=uuid5(run.id, str(index)),
                    workflow_id=reference.workflow_id,
                    environment_id=frozen.environment_id,
                    principal_id=run.principal_id,
                    operation_id=f"asset:{run.id}:{index}",
                    action="write",
                )
                for index, _ in enumerate(frozen.assets)
            ]

    def stage(self, command: RuntimeCommand, index: int) -> RuntimeCommand:
        r = self.runs
        with r.sessions() as session, session.begin():
            run_id = command.operation_id.split(":")[1]
            prior = session.get(Run, UUID(run_id))
            if prior is None:
                raise ConflictError("The Run does not exist")
            run = load_run(
                session,
                run_id=prior.id,
                subject=ExecutionSubject(prior.principal_id),
                admission=r.admission,
                lock=True,
            )
            if run.status != "running":
                raise ConflictError("The Run may not transfer assets")
            request = ExecutionRequest.model_validate(
                r.cipher.decrypt_payload(run.request_ciphertext)
            )
            asset = request.assets[index]
            model_session = session.get(SessionRecord, run.session_id)
            assert model_session is not None
            payload = self.assets.read_asset(
                session,
                subject=ExecutionSubject(run.principal_id),
                scope=record_scope(model_session, principal_id=run.principal_id),
                ref=asset.ref,
                digest=asset.digest,
                destination=asset.destination,
            )
            if len(payload) > 4_000_000:
                raise ConflictError("The asset exceeds the managed transfer limit")
            environment = load_environment(
                session,
                environment_id=command.environment_id,
                subject=ExecutionSubject(run.principal_id),
                admission=r.admission,
                lock=True,
                shared_use=True,
            )
            if environment.scope_id != run.scope_id or environment.owner_ref != run.owner_ref:
                raise ForbiddenError("The Run environment belongs to another scope")
            arguments: dict[str, object] = {
                "path": asset.destination,
                "content_base64": base64.b64encode(payload).decode("ascii"),
            }
            fingerprint = payload_digest(arguments)
            previous = session.get(
                EnvironmentOperationReceipt, (environment.scope_id, command.operation_id)
            )
            if previous is not None:
                if previous.fingerprint != fingerprint or previous.run_id != run.id:
                    raise ConflictError("The frozen asset transfer identity changed")
            else:
                if environment.status != "ready" or environment.active_operation_id is not None:
                    raise ConflictError("The environment is busy or unavailable")
                stage_operation(
                    session,
                    record=environment,
                    subject=ExecutionSubject(run.principal_id),
                    operation_id=command.operation_id,
                    action="write",
                    arguments=arguments,
                    fingerprint=fingerprint,
                    dispatch=False,
                    run_id=run.id,
                )
            return command.model_copy(update={"arguments": arguments})

    @activity.defn(name="orchestration.assets")
    async def transfer(self, reference: RunReference) -> None:
        commands = await asyncio.to_thread(self.commands, reference)
        for index, command in enumerate(commands):
            staged = await asyncio.to_thread(self.stage, command, index)
            result = await self.runtime.execute(staged)
            if (
                result.status != "completed"
                or result.result is None
                or "bytes_written" not in result.result
            ):
                raise ConflictError("The native asset transfer did not confirm its effect")

    def output_commands(self, reference: RunReference) -> list[RuntimeCommand]:
        r = self.runs
        with r.sessions() as session, session.begin():
            prior = session.get(Run, reference.run_id)
            if prior is None:
                raise ConflictError("The Run does not exist")
            run = load_run(
                session,
                run_id=prior.id,
                subject=ExecutionSubject(prior.principal_id),
                admission=r.admission,
                lock=True,
            )
            request = ExecutionRequest.model_validate(
                r.cipher.decrypt_payload(run.request_ciphertext)
            )
            if run.status != "running" or request.environment_id is None:
                raise ConflictError("The Run may not collect outputs")
            environment = load_environment(
                session,
                environment_id=request.environment_id,
                subject=ExecutionSubject(run.principal_id),
                admission=r.admission,
                lock=True,
                shared_use=True,
            )
            if environment.scope_id != run.scope_id or environment.owner_ref != run.owner_ref:
                raise ForbiddenError("The output environment belongs to another scope")
            return [
                RuntimeCommand(
                    execution_id=uuid5(run.id, f"output:{index}"),
                    workflow_id=reference.workflow_id,
                    environment_id=environment.id,
                    principal_id=run.principal_id,
                    operation_id=f"output:{run.id}:{index}",
                    action="read",
                    arguments={"path": item.path},
                )
                for index, item in enumerate(request.outputs)
            ]

    def stage_output(self, reference: RunReference, command: RuntimeCommand) -> None:
        r = self.runs
        with r.sessions() as session, session.begin():
            run = load_run(
                session,
                run_id=reference.run_id,
                subject=ExecutionSubject(command.principal_id),
                admission=r.admission,
                lock=True,
            )
            if run.status != "running":
                raise ConflictError("The Run may not collect outputs")
            environment = load_environment(
                session,
                environment_id=command.environment_id,
                subject=ExecutionSubject(run.principal_id),
                admission=r.admission,
                lock=True,
                shared_use=True,
            )
            previous = session.get(
                EnvironmentOperationReceipt, (environment.scope_id, command.operation_id)
            )
            if previous is None:
                if environment.status != "ready" or environment.active_operation_id is not None:
                    raise ConflictError("The output environment is busy")
                stage_operation(
                    session,
                    record=environment,
                    subject=ExecutionSubject(run.principal_id),
                    operation_id=command.operation_id,
                    action="read",
                    arguments=command.arguments,
                    fingerprint=payload_digest(command.arguments),
                    dispatch=False,
                    run_id=run.id,
                )

    def publish(self, reference: RunReference, index: int, content: str) -> UUID:
        r = self.runs
        with r.sessions() as session, session.begin():
            prior = session.get(Run, reference.run_id)
            if prior is None:
                raise ConflictError("The Run does not exist")
            run = load_run(
                session,
                run_id=prior.id,
                subject=ExecutionSubject(prior.principal_id),
                admission=r.admission,
                lock=True,
            )
            if run.status != "running":
                raise ConflictError("The Run may not publish outputs")
            request = ExecutionRequest.model_validate(
                r.cipher.decrypt_payload(run.request_ciphertext)
            )
            declaration = request.outputs[index]
            return self.assets.publish_output(
                session,
                subject=ExecutionSubject(run.principal_id),
                run_id=run.id,
                name=declaration.name,
                payload=base64.b64decode(content, validate=True),
                media_type=declaration.media_type,
            )

    @activity.defn(name="orchestration.outputs")
    async def collect(self, reference: RunReference) -> list[UUID]:
        result = []
        for index, command in enumerate(await asyncio.to_thread(self.output_commands, reference)):
            await asyncio.to_thread(self.stage_output, reference, command)
            read = await self.runtime.execute(command)
            if read.result is None or not isinstance(read.result.get("content_base64"), str):
                raise ConflictError("The native output read did not confirm its content")
            result.append(
                await asyncio.to_thread(
                    self.publish, reference, index, read.result["content_base64"]
                )
            )
        return result
