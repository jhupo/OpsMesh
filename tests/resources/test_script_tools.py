"""Real local venv installation from an artifact wheel, JSON execution and gateway replay."""

import zipfile

import pytest

from opsmesh.framework.errors import OpsMeshError
from opsmesh.infrastructure.database import AsyncDatabase
from opsmesh.modules.capabilities.api import ScriptToolSource
from opsmesh.modules.capabilities.internal.catalog import PersistentCapabilityCatalog
from opsmesh.modules.capabilities.internal.code_tools import InProcessCodeToolRegistry
from opsmesh.modules.capabilities.internal.scripts import PersistentScriptTools
from opsmesh.modules.capabilities.internal.tool_invoker import AuthorizedToolInvoker
from opsmesh.modules.execution.api import ExecutionSpec
from opsmesh.modules.execution.internal.service import LocalExecutionProvider
from opsmesh.modules.workspaces.internal.service import LocalWorkspaceService


async def authorize(principal, resource):
    if principal != "alice":
        raise OpsMeshError("AUTHORIZATION_DENIED", "Denied")


def bundle(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    wheel = "example_math-1.0-py3-none-any.whl"
    with zipfile.ZipFile(source / wheel, "w") as artifact:
        artifact.writestr("example_math/__init__.py", "def double(value): return value * 2\n")
        artifact.writestr(
            "example_math-1.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: example-math\nVersion: 1.0\n",
        )
        artifact.writestr(
            "example_math-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: opsmesh-test\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        artifact.writestr("example_math-1.0.dist-info/RECORD", "")
    (source / "main.py").write_text(
        "import sys, json\nfrom example_math import double\n"
        "from pathlib import Path\n"
        "value=json.loads(Path(sys.argv[1]).read_text())\n"
        "print(json.dumps({'result': double(value['number'])}))\n",
        encoding="utf-8",
    )
    return source, ScriptToolSource(
        name="double",
        version="1",
        entrypoint="main.py",
        wheels=(wheel,),
        input_schema={
            "type": "object",
            "properties": {"number": {"type": "integer"}},
            "required": ["number"],
        },
    )


@pytest.fixture
async def script_service(tmp_path):
    database = AsyncDatabase(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    workspaces = LocalWorkspaceService(database, tmp_path / "objects")
    catalog = PersistentCapabilityCatalog(database)
    scripts = PersistentScriptTools(
        database,
        workspaces,
        catalog,
        authorize,
        execution_provider=LocalExecutionProvider(),
        execution_template=ExecutionSpec(workspace=tmp_path, trusted_local=True),
    )
    invoker = AuthorizedToolInvoker(
        database, catalog, InProcessCodeToolRegistry(), authorize, scripts=scripts
    )
    await database.initialize()
    try:
        yield scripts, invoker, workspaces
    finally:
        await database.close()


async def test_offline_wheel_environment_executes_and_gateway_deduplicates(
    script_service, tmp_path
):
    scripts, invoker, _ = script_service
    source, declaration = bundle(tmp_path)
    published = await scripts.publish(source, declaration)
    assert published.ref == "double@1" and len(published.files) == 2
    assert await invoker.invoke(
        "double@1", {"number": 21}, principal_id="alice", call_id="one"
    ) == {"result": 42}
    # Destroy source after publication. The immutable artifact and persisted result own execution.
    (source / "main.py").write_text("raise RuntimeError('modified')", encoding="utf-8")
    assert await invoker.invoke(
        "double@1", {"number": 21}, principal_id="alice", call_id="one"
    ) == {"result": 42}
    with pytest.raises(OpsMeshError) as replay:
        await invoker.invoke("double@1", {"number": 22}, principal_id="alice", call_id="one")
    assert replay.value.code == "CONFLICT"
    with pytest.raises(OpsMeshError) as immutable:
        await scripts.publish(source, declaration)
    assert immutable.value.code == "CONFLICT"
    with pytest.raises(OpsMeshError):
        await scripts.execute(published.ref, {"number": 21}, principal_id="bob")


async def test_script_timeout_cleans_execution_and_does_not_retry_side_effects(
    script_service, tmp_path
):
    scripts, invoker, _ = script_service
    source = tmp_path / "slow"
    source.mkdir()
    (source / "main.py").write_text("import time\ntime.sleep(10)\nprint('{}')", encoding="utf-8")
    await scripts.publish(
        source,
        ScriptToolSource(
            name="slow",
            version="1",
            entrypoint="main.py",
            input_schema={"type": "object"},
            timeout_seconds=0.1,
        ),
    )
    with pytest.raises(OpsMeshError) as timeout:
        await invoker.invoke("slow@1", {}, principal_id="alice", call_id="slow")
    assert timeout.value.code == "EXECUTION_TIMEOUT"
    with pytest.raises(OpsMeshError) as replay:
        await invoker.invoke("slow@1", {}, principal_id="alice", call_id="slow")
    assert replay.value.code == "TOOL_REPLAY_BLOCKED"


async def test_script_artifact_tampering_is_rejected_before_execution(script_service, tmp_path):
    scripts, _, _ = script_service
    source, declaration = bundle(tmp_path)
    published = await scripts.publish(source, declaration)
    asset = published.files[0]
    (tmp_path / "objects" / "objects" / asset.digest).write_bytes(b"corrupt")
    with pytest.raises(OpsMeshError) as corrupt:
        await scripts.execute(published.ref, {"number": 2}, principal_id="alice")
    assert corrupt.value.code == "CONTENT_CORRUPT"
