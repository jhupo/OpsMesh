"""Provider contracts execute registered clients and authorize manifests before allocation."""

from types import SimpleNamespace

import pytest
from agents.sandbox import Manifest

from opsmesh.framework.agents import AgentExecutionContext, SandboxConfiguration
from opsmesh.framework.agents.resources import SandboxProviderBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.internal.backends.openai_agents.sandbox_lifecycle import (
    ManagedSandboxSession,
)


class Session:
    def __init__(self, actions):
        self.actions = actions

    def _set_concurrency_limits(self, limits):
        self.concurrency = limits

    def _set_archive_limits(self, limits):
        self.archive = limits

    async def start(self):
        self.actions.append("start")

    async def aclose(self):
        self.actions.append("close")


class Client:
    def __init__(self, actions):
        self.actions = actions
        self.created = []
        self.resumed = []

    async def create(self, **values):
        self.actions.append("create")
        self.created.append(values)
        return Session(self.actions)

    async def resume(self, state):
        self.actions.append("resume")
        self.resumed.append(state)
        return Session(self.actions)

    async def delete(self, session):
        self.actions.append("delete")

    def deserialize_session_state(self, state):
        return ManifestState(Manifest(), state)


class ManifestState:
    def __init__(self, manifest, payload):
        self.manifest, self.payload = manifest, payload

    def model_copy(self, *, update):
        return ManifestState(update.get("manifest", self.manifest), self.payload)


async def unused(*args):
    raise AssertionError("No model call")


@pytest.mark.asyncio
async def test_registered_provider_receives_options_and_validated_mount_manifest():
    actions = []
    client = Client(actions)
    context = AgentExecutionContext("run", "session", "alice", unused)

    async def authorize(manifest, execution_context):
        assert execution_context.principal_id == "alice"
        assert manifest["entries"]["resource"]["src"] == "/trusted/data"
        actions.append("authorize")

    configuration = SandboxConfiguration(
        provider="cloud-plugin@1",
        manifest={"entries": {"resource": {"type": "local_file", "src": "/trusted/data"}}},
    )
    options = SimpleNamespace(cpu=2, memory=1024)
    manager = await ManagedSandboxSession.open(
        configuration,
        (),
        (),
        context=context,
        provider=SandboxProviderBinding(client, options, authorize),
    )
    assert client.created[0]["options"] is options
    assert client.created[0]["manifest"].entries["resource"].src.as_posix() == "/trusted/data"
    await manager.close()
    assert actions == ["authorize", "create", "start", "close", "delete"]


@pytest.mark.asyncio
async def test_provider_manifest_denial_happens_before_client_create():
    actions = []
    client = Client(actions)

    async def deny(*args):
        raise OpsMeshError("AUTHORIZATION_DENIED", "Mount is outside deployment allowlist")

    with pytest.raises(OpsMeshError) as error:
        await ManagedSandboxSession.open(
            SandboxConfiguration(provider="cloud-plugin@1"),
            (),
            (),
            context=AgentExecutionContext("run", "session", "alice", unused),
            provider=SandboxProviderBinding(client, validate_manifest=deny),
        )
    assert error.value.code == "AUTHORIZATION_DENIED" and actions == []


@pytest.mark.asyncio
async def test_provider_without_manifest_policy_cannot_authorize_host_mount():
    client = Client([])
    with pytest.raises(OpsMeshError) as error:
        await ManagedSandboxSession.open(
            SandboxConfiguration(
                provider="cloud-plugin@1",
                manifest={"entries": {"resource": {"type": "local_file", "src": "/secret"}}},
            ),
            (),
            (),
            provider=SandboxProviderBinding(client),
        )
    assert error.value.code == "FORBIDDEN" and client.created == []
