"""Actual Temporal persistence, control delivery, cancellation and replay with real PG."""

import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from opsmesh.orchestration.definitions.contracts import FlowReference, FlowSignal
from opsmesh.orchestration.runs.flow_workflow import FlowWorkflow
from opsmesh.orchestration.runs.models import FlowRun
from opsmesh.orchestration.runs.schemas import FlowControl
from opsmesh.orchestration.runs.service import control_run, submit_signal
from tests.orchestration.test_flows_postgres import create_flow
from tests.orchestration.test_flows_postgres import flow_facts as flow_facts


def test_pause_signal_resume_and_replay(database_sessions, flow_facts):  # noqa: F811
    runtime, actor, _ = flow_facts
    identifier = create_flow(database_sessions, flow_facts)

    async def verify():
        async with await WorkflowEnvironment.start_local(
            data_converter=pydantic_data_converter
        ) as environment:
            queue = f"flow-{uuid4()}"
            activities = [
                runtime.load,
                runtime.project,
                runtime.begin_step,
                runtime.probe_step,
                runtime.complete_step,
                runtime.cancel_steps,
            ]
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[FlowWorkflow],
                activities=activities,
            ):
                handle = await environment.client.start_workflow(
                    FlowWorkflow.run,
                    FlowReference(
                        execution_id=identifier,
                        run_id=identifier,
                        workflow_id=f"flow:{identifier}:1",
                    ),
                    id=f"flow:{identifier}:1",
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=45),
                )
                await asyncio.sleep(1)
                with database_sessions() as session, session.begin():
                    run = session.get(FlowRun, identifier)
                    control_run(
                        session,
                        actor=actor,
                        run_id=identifier,
                        cipher=runtime.cipher,
                        request=FlowControl(
                            command_id=uuid4(), expected_revision=run.revision, action="pause"
                        ),
                    )
                    submit_signal(
                        session,
                        actor=actor,
                        run_id=identifier,
                        cipher=runtime.cipher,
                        request=FlowSignal(
                            command_id=uuid4(),
                            step_path="signal",
                            generation=1,
                            name="answer",
                            value={"received": 1},
                        ),
                    )
                await handle.signal(FlowWorkflow.wake)
                await asyncio.sleep(1)
                with database_sessions() as session, session.begin():
                    run = session.get(FlowRun, identifier)
                    assert run.status == "paused"
                    control_run(
                        session,
                        actor=actor,
                        run_id=identifier,
                        cipher=runtime.cipher,
                        request=FlowControl(
                            command_id=uuid4(), expected_revision=run.revision, action="resume"
                        ),
                    )
                await handle.signal(FlowWorkflow.wake)
                answer = await handle.result()
                assert answer["status"] == "succeeded"
                with database_sessions() as session:
                    assert runtime.cipher.decrypt_payload(
                        session.get(FlowRun, identifier).output_ciphertext
                    ) == {"value": {"received": 1}}
                history = await handle.fetch_history()
            await Replayer(
                workflows=[FlowWorkflow], data_converter=pydantic_data_converter
            ).replay_workflow(history)

    asyncio.run(verify())


@pytest.mark.parametrize("join,quorum", [("any", 1), ("quorum", 2)])
def test_bounded_composites_early_join_and_replay(
    database_sessions,
    flow_facts,  # noqa: F811
    join,
    quorum,
):
    from sqlalchemy import select

    from opsmesh.orchestration.definitions.contracts import (
        FlowCondition,
        FlowDefinitionSpec,
        FlowGraph,
        FlowNode,
    )
    from opsmesh.orchestration.definitions.schemas import DefinitionCreate
    from opsmesh.orchestration.definitions.service import create_definition, publish_definition
    from opsmesh.orchestration.runs.models import StepAttempt
    from opsmesh.orchestration.runs.schemas import FlowRunCreate
    from opsmesh.orchestration.runs.service import start_run

    runtime, actor, _ = flow_facts
    check = FlowGraph(
        nodes=[
            FlowNode(
                key="check",
                kind="condition",
                condition=FlowCondition(operator="eq", left={"$ref": "item"}, right=1),
            ),
            FlowNode(
                key="receipt",
                kind="wait",
                wait_kind="external",
                external_step="check",
                depends_on=["check"],
            ),
        ],
        output={"$ref": "steps.receipt"},
    )
    repeat = FlowGraph(
        nodes=[
            FlowNode(
                key="check",
                kind="condition",
                condition=FlowCondition(operator="ge", left={"$ref": "index"}, right=1),
            )
        ],
        output={"$ref": "steps.check"},
    )
    graph = FlowGraph(
        nodes=[
            FlowNode(
                key="each",
                kind="foreach",
                arguments={"items": [1, 2, 1]},
                max_iterations=3,
                concurrency=2,
                body=check,
            ),
            FlowNode(
                key="repeat",
                kind="repeat",
                max_iterations=3,
                body=repeat,
                condition=FlowCondition(operator="eq", left={"$ref": "input"}, right=True),
            ),
            FlowNode(
                key="parallel",
                kind="parallel",
                join=join,
                quorum=quorum,
                concurrency=2,
                body=FlowGraph(
                    nodes=[
                        FlowNode(
                            key="fast",
                            kind="condition",
                            condition=FlowCondition(operator="eq", left=1, right=1),
                        ),
                        FlowNode(
                            key="fast_done",
                            kind="condition",
                            depends_on=["fast"],
                            condition=FlowCondition(operator="eq", left=2, right=2),
                        ),
                        FlowNode(key="slow", kind="wait", wait_seconds=60),
                        *(
                            [
                                FlowNode(
                                    key="other",
                                    kind="condition",
                                    condition=FlowCondition(operator="eq", left=2, right=2),
                                )
                            ]
                            if join == "quorum"
                            else []
                        ),
                    ]
                ),
            ),
            FlowNode(key="join", kind="join", depends_on=["each", "repeat", "parallel"]),
        ],
        output={"$ref": "steps.join"},
    )
    with database_sessions() as session, session.begin():
        definition = create_definition(
            session,
            actor=actor,
            request=DefinitionCreate(name="Control nodes", spec=FlowDefinitionSpec(graph=graph)),
        )
        revision = publish_definition(
            session, actor=actor, definition_id=definition.id, expected_revision=1
        )
        record = start_run(
            session,
            actor=actor,
            request=FlowRunCreate(revision_id=revision.id, input={}),
            idempotency_key="composites",
            cipher=runtime.cipher,
        )
        identifier = record.id

    async def verify():
        async with await WorkflowEnvironment.start_local(
            data_converter=pydantic_data_converter
        ) as environment:
            queue = f"composite-{uuid4()}"
            async with Worker(
                environment.client,
                task_queue=queue,
                workflows=[FlowWorkflow],
                activities=[
                    runtime.load,
                    runtime.project,
                    runtime.begin_step,
                    runtime.probe_step,
                    runtime.complete_step,
                    runtime.cancel_steps,
                ],
            ):
                handle = await environment.client.start_workflow(
                    FlowWorkflow.run,
                    FlowReference(
                        execution_id=identifier,
                        run_id=identifier,
                        workflow_id=f"flow:{identifier}:1",
                    ),
                    id=f"flow:{identifier}:1",
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=45),
                )
                result = await handle.result()
                assert result["status"] == "succeeded", result
                assert result["output"]["each"] == [True, False, True]
                assert result["output"]["repeat"] == [False, True]
                assert result["output"]["parallel"] == (
                    {"fast_done": True, "other": True} if join == "quorum" else {"fast_done": True}
                )
                history = await handle.fetch_history()
            await Replayer(
                workflows=[FlowWorkflow], data_converter=pydantic_data_converter
            ).replay_workflow(history)

    asyncio.run(verify())
    with database_sessions() as session:
        attempts = list(
            session.scalars(select(StepAttempt).where(StepAttempt.run_id == identifier))
        )
        assert all(item.status in {"completed", "cancelled"} for item in attempts)
        assert session.get(FlowRun, identifier).active_steps == 0


def test_cancel_boundary_prevents_a_late_branch_start(database_sessions, flow_facts):  # noqa: F811
    from sqlalchemy import func, select

    from opsmesh.orchestration.definitions.contracts import FlowNode, StepCommand
    from opsmesh.orchestration.runs.models import StepAttempt

    runtime, _, _ = flow_facts
    identifier = create_flow(database_sessions, flow_facts)
    assert runtime.cancel_children(identifier, "signal") == {"pending": 0, "uncertain": 0}
    result = runtime.start_step(
        StepCommand(
            run_id=identifier,
            path="signal",
            node=FlowNode(key="signal", kind="wait", wait_kind="signal", signal_name="answer"),
            arguments={},
        )
    )
    assert result["status"] == "cancelled"
    with database_sessions() as session:
        assert session.scalar(select(func.count()).select_from(StepAttempt)) == 0


def test_durable_wait_survives_worker_restart(database_sessions, flow_facts):  # noqa: F811
    runtime, actor, _ = flow_facts
    identifier = create_flow(database_sessions, flow_facts)

    async def verify():
        async with await WorkflowEnvironment.start_local(
            data_converter=pydantic_data_converter
        ) as environment:
            queue = f"restart-{uuid4()}"

            def worker():
                return Worker(
                    environment.client,
                    task_queue=queue,
                    workflows=[FlowWorkflow],
                    activities=[
                        runtime.load,
                        runtime.project,
                        runtime.begin_step,
                        runtime.probe_step,
                        runtime.complete_step,
                        runtime.cancel_steps,
                    ],
                )

            async with worker():
                handle = await environment.client.start_workflow(
                    FlowWorkflow.run,
                    FlowReference(
                        execution_id=identifier,
                        run_id=identifier,
                        workflow_id=f"flow:{identifier}:1",
                    ),
                    id=f"flow:{identifier}:1",
                    task_queue=queue,
                    execution_timeout=timedelta(seconds=45),
                )
                for _ in range(100):
                    if runtime.load_state(identifier)["waiting"]:
                        break
                    await asyncio.sleep(0.1)
                assert runtime.load_state(identifier)["waiting"]
            # Deliver while there is no Worker; Temporal retains the wake and wait history.
            with database_sessions() as session, session.begin():
                submit_signal(
                    session,
                    actor=actor,
                    run_id=identifier,
                    cipher=runtime.cipher,
                    request=FlowSignal(
                        command_id=uuid4(),
                        step_path="signal",
                        generation=1,
                        name="answer",
                        value={"after_restart": True},
                    ),
                )
            await handle.signal(FlowWorkflow.wake)
            async with worker():
                result = await handle.result()
                assert result == {"status": "succeeded", "output": {"after_restart": True}}
                history = await handle.fetch_history()
            await Replayer(
                workflows=[FlowWorkflow], data_converter=pydantic_data_converter
            ).replay_workflow(history)

    asyncio.run(verify())
