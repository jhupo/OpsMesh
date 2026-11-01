"""Public hosting, credentials and CLI lifecycle work without a project-specific runner."""

import json
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from opsmesh import OpsMeshSettings, create_application
from opsmesh.application.deployment import ApplicationDefinition
from opsmesh.entrypoints.cli.applications import _execute, run_application
from opsmesh.framework.adapters import BotConnectionConfiguration
from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.secrets import DeploymentSecretResolver
from opsmesh.modules.capabilities.api import ToolDefinition


def test_framework_import_does_not_compose_platform_services():
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import opsmesh.framework; "
            "assert 'opsmesh.bootstrap.application' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_constructor_credentials_are_scoped_and_environment_is_unchanged(monkeypatch):
    monkeypatch.delenv("BOT_KEY", raising=False)
    supplied = {"qq-secret": SecretStr("only-in-this-application")}
    resolver = DeploymentSecretResolver(supplied)
    supplied.clear()
    assert resolver.resolve("runtime:qq-secret") == "only-in-this-application"
    with pytest.raises(OpsMeshError):
        DeploymentSecretResolver().resolve("runtime:qq-secret")
    with pytest.raises(OpsMeshError):
        resolver.resolve("env:BOT_KEY")
    account = BotConnectionConfiguration(
        id="qq", adapter="qq", self_id="123", credentials={"client_secret": "runtime:qq-secret"}
    )
    assert "only-in-this-application" not in account.model_dump_json()
    assert "only-in-this-application" not in repr(resolver)


async def test_public_application_closes_on_factory_failure(tmp_path):
    app = create_application(OpsMeshSettings(data_directory=tmp_path, worker_enabled=False))
    with pytest.raises(RuntimeError, match="configuration failed"):
        async with app:
            raise RuntimeError("configuration failed")
    assert app.services.http_client.is_closed
    await app.close()


async def test_register_tool_owns_one_schema_and_preserves_atomic_registration(tmp_path):
    definition = ToolDefinition(
        name="inspect",
        version="1",
        code_ref="example:inspect",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )

    async def inspect(arguments):
        return {"result": "ok"}

    async with create_application(OpsMeshSettings(data_directory=tmp_path)) as app:
        app.register_tool(definition, inspect)
        app.define(ApplicationDefinition())
        assert app.publication.definition.tools == (definition,)
        assert definition.bind().input_schema == definition.input_schema
        assert await app.services.code_tools.resolve(definition.code_ref)({}) == {"result": "ok"}
        conflicting = definition.model_copy(update={"description": "different contract"})
        with pytest.raises(OpsMeshError):
            app.define(ApplicationDefinition(tools=(conflicting,)))
        with pytest.raises(OpsMeshError):
            app.define(ApplicationDefinition(tools=(definition, definition)))
        with pytest.raises(OpsMeshError):
            app.register_tool(definition.model_copy(update={"name": "duplicate_code"}), inspect)
        assert app.publication.definition.tools == (definition,)


@pytest.mark.parametrize("config", [{"unexpected": "do-not-display-this"}, []])
def test_cli_rejects_bad_parameters_before_opening_application(tmp_path, monkeypatch, config):
    path = tmp_path / "parameters.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["example", "serve", "--config", str(path)])

    @asynccontextmanager
    async def factory(*, expected: str):
        pytest.fail("Invalid factory parameters must not construct the application")
        yield

    with pytest.raises(SystemExit) as error:
        run_application(factory)
    assert "do-not-display-this" not in str(error.value)


@pytest.mark.parametrize("failure", [False, True])
async def test_publish_logs_out_and_closes_factory_resources_on_failure(failure):
    events = []
    accounts = SimpleNamespace(
        login=AsyncMock(
            return_value=SimpleNamespace(token="login-token", principal=SimpleNamespace(id="actor"))
        ),
        logout=AsyncMock(),
    )
    publish = AsyncMock(
        side_effect=OpsMeshError("FORBIDDEN", "Denied") if failure else None,
        return_value=SimpleNamespace(agents={"assistant": "assistant@1"}),
    )
    app = SimpleNamespace(
        services=SimpleNamespace(
            database=SimpleNamespace(verify_schema=AsyncMock()), accounts=accounts
        ),
        publish=publish,
    )

    @asynccontextmanager
    async def factory():
        events.append("opened")
        try:
            yield app
        finally:
            events.append("closed")

    if failure:
        with pytest.raises(OpsMeshError):
            await _execute(
                factory, {}, command="publish", username="admin", password="synthetic-password"
            )
    else:
        await _execute(
            factory, {}, command="publish", username="admin", password="synthetic-password"
        )
    accounts.logout.assert_awaited_once_with("login-token")
    publish.assert_awaited_once_with(actor_id="actor")
    assert events == ["opened", "closed"]
