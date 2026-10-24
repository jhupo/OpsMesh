"""Snapshots refuse current workflow runs and unfinished sandbox operations."""

import pytest
from opsmesh_operator import backups
from sqlalchemy import select

from opsmesh.bootstrap.policies import PLATFORM_REDACTION
from opsmesh.execution.runtime.models import SandboxEnvironment, SandboxOperation
from opsmesh.execution.runtime.schemas import EnvironmentCreate
from opsmesh.execution.runtime.service import create_environment
from opsmesh.users.quotas.service import ensure_capacity_accounts
from opsmesh.users.service import create_user, identity_of
from opsmesh.workflows.instances.schemas import InstanceCreate
from opsmesh.workflows.instances.service import create_instance
from opsmesh.workflows.publication import publish_workflow
from opsmesh.workflows.runs.models import WorkflowRun
from opsmesh.workflows.runs.schemas import RunCreate
from opsmesh.workflows.runs.service import finish_run, submit_run
from opsmesh.workflows.schemas import WorkflowCreate
from opsmesh.workflows.service import create_workflow
from tests.conftest import dispatch_cipher


def test_snapshot_requires_terminal_runs_and_closed_environments(database_sessions, monkeypatch):
    url = database_sessions.kw["bind"].url
    monkeypatch.setattr(
        backups,
        "postgres_env",
        lambda _: {
            "PGHOST": url.host,
            "PGPORT": str(url.port or 5432),
            "PGUSER": url.username,
            "PGPASSWORD": url.password,
            "PGDATABASE": url.database,
        },
    )
    cipher = dispatch_cipher()
    backups.require_quiescent_execution(None)
    with database_sessions.begin() as session:
        actor = identity_of(
            create_user(session, email="snapshot@example.test", display_name="Owner")
        )
        ensure_capacity_accounts(
            session, user_id=actor.user_id, limits={"concurrency": 2, "environment": 2}
        )
        definition = create_workflow(
            session,
            actor=actor,
            request=WorkflowCreate.model_validate(
                {
                    "name": "Snapshot",
                    "spec": {
                        "graph": {
                            "nodes": [{"key": "wait", "kind": "wait", "seconds": 1}],
                        }
                    },
                }
            ),
            redaction=PLATFORM_REDACTION,
        )
        revision = publish_workflow(
            session,
            actor=actor,
            workflow_id=definition.id,
            expected_revision=definition.revision,
            visibility="private",
            redaction=PLATFORM_REDACTION,
        )
        instance = create_instance(
            session,
            actor=actor,
            cipher=cipher,
            redaction=PLATFORM_REDACTION,
            request=InstanceCreate(
                revision_id=revision.id, name="Snapshot", request_key="instance"
            ),
        )
        run_id = submit_run(
            session,
            actor=actor,
            cipher=cipher,
            request=RunCreate(instance_id=instance.id, request_key="run", input={}),
        ).id
    with pytest.raises(RuntimeError, match="Snapshot refused"):
        backups.require_quiescent_execution(None)
    with database_sessions.begin() as session:
        finish_run(
            session,
            row=session.get(WorkflowRun, run_id),
            status="completed",
            output={},
            cipher=cipher,
        )
    backups.require_quiescent_execution(None)
    with database_sessions.begin() as session:
        environment_id = create_environment(
            session,
            actor=actor,
            cipher=cipher,
            request=EnvironmentCreate(image="approved:1"),
            allowed_images={"approved:1"},
        ).id
    with pytest.raises(RuntimeError, match="close sandbox"):
        backups.require_quiescent_execution(None)
    with database_sessions.begin() as session:
        session.get(SandboxEnvironment, environment_id).status = "closed"
    # A closed environment alone does not settle its queued operation receipt.
    with pytest.raises(RuntimeError, match="Snapshot refused"):
        backups.require_quiescent_execution(None)
    with database_sessions.begin() as session:
        for operation in session.scalars(select(SandboxOperation)):
            operation.status = "rejected"
    backups.require_quiescent_execution(None)
