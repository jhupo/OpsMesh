"""Runtime API persists environments and dispatches commands without creating hosts."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from opsmesh.runtime.instances.models import RuntimeCommand, RuntimeTemplate
from opsmesh.runtime.sandboxes.models import SandboxReference
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.models import TemporalOutboxEntry
from tests.fixtures.agent_api import TOKEN
from tests.fixtures.agent_product import _seed_workspace

pytest_plugins = ["tests.fixtures.agent_api"]


def headers(user):
    return {"Authorization": f"Bearer {TOKEN}", "X-User-ID": str(user.id)}


def seed(client, session):
    user, workspace = _seed_workspace(session, with_default_provider=False)
    image = "python:3.13"
    client.app.state.settings.runtime_allowed_images = [image]
    template = RuntimeTemplate(
        name="Native", image=image, sandbox_options={}, created_at=datetime.now(UTC)
    )
    session.add(template)
    session.commit()
    return user, workspace, template


def test_runtime_lifecycle_and_command_intents_are_workspace_scoped(agent_api):
    client, session = agent_api
    user, workspace, template = seed(client, session)
    prefix = f"/api/v1/workspaces/{workspace.id}"
    response = client.post(
        f"{prefix}/runtimes",
        headers=headers(user),
        json={"name": "Project", "template_id": str(template.id)},
    )
    assert response.status_code == 201, response.text
    runtime = response.json()
    assert runtime["status"] == "active" and runtime["sandbox_backend_id"] == "docker"
    assert "host" not in runtime and "execution_uid" not in runtime
    assert "network_mode" not in runtime["sandbox_options"]
    assert session.scalar(select(SandboxReference)) is None
    identifier = runtime["id"]
    response = client.post(
        f"{prefix}/runtimes/{identifier}/commands",
        headers=headers(user),
        json={"command": ["python", "--version"]},
    )
    assert response.status_code == 202, response.text
    command = response.json()
    assert command["status"] in {"queued", "waiting_approval"}
    if command["status"] == "queued":
        dispatch = session.scalars(select(TemporalOutboxEntry)).one()
        request = ExecutionRequest.model_validate(dispatch.payload["execution"])
        assert request.kind == ExecutionKind.SANDBOX_CONTROL
        assert str(request.execution_id) == command["id"] == str(request.resource_id)
    from opsmesh.identity.users.models import User
    from opsmesh.workspaces.management.models import Workspace

    other_user = User(email="other@runtime.test", display_name="Other")
    other_workspace = Workspace(owner=other_user, name="Other", slug="other")
    session.add_all([other_user, other_workspace])
    session.commit()
    foreign = client.get(
        f"/api/v1/workspaces/{other_workspace.id}/runtimes/{identifier}/commands",
        headers=headers(user),
    )
    assert foreign.status_code in {403, 404}
    response = client.post(f"{prefix}/runtimes/{identifier}/disable", headers=headers(user))
    assert response.status_code == 200 and response.json()["status"] == "disabled"
    denied = client.post(
        f"{prefix}/runtimes/{identifier}/commands",
        headers=headers(user),
        json={"command": ["python", "--version"]},
    )
    assert denied.status_code == 403, denied.text
    assert len(session.scalars(select(RuntimeCommand)).all()) == 1
    cleanup = [
        ExecutionRequest.model_validate(row.payload["execution"])
        for row in session.scalars(select(TemporalOutboxEntry))
    ]
    assert any(item.kind == ExecutionKind.SANDBOX_CLEANUP for item in cleanup)


def test_template_image_and_foreign_space_rejections(agent_api):
    client, session = agent_api
    user, workspace, template = seed(client, session)
    prefix = f"/api/v1/workspaces/{workspace.id}/runtimes"
    rejected = client.post(
        prefix,
        headers=headers(user),
        json={"name": "foreign", "template_id": str(template.id), "runtime_space_id": str(uuid4())},
    )
    assert rejected.status_code == 404, rejected.text
    template.image = "unapproved:latest"
    session.commit()
    rejected = client.post(
        prefix, headers=headers(user), json={"name": "unapproved", "template_id": str(template.id)}
    )
    assert rejected.status_code == 403, rejected.text
    legacy = client.post(
        prefix,
        headers=headers(user),
        json={"name": "old", "template_id": str(template.id), "limits": {"cpu_count": 1}},
    )
    assert legacy.status_code == 422
