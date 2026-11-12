import os
import subprocess
import sys
from pathlib import Path

import anyio
import pytest

from opsmesh.framework import config
from opsmesh.framework import lifecycle as _lifespan


@pytest.mark.anyio
async def test_lifecycle_callbacks_preserve_order_and_reset_task_group():
    lifespan = _lifespan.Lifespan()
    events = []
    lifespan.on_startup(lambda: events.append("startup"))

    async def ready():
        events.append("ready")

    lifespan.on_ready(ready)
    lifespan.on_shutdown(lambda: events.append("first shutdown"))
    lifespan.on_shutdown(lambda: events.append("second shutdown"))
    async with lifespan:
        events.append("running")
    assert events == [
        "startup",
        "ready",
        "running",
        "second shutdown",
        "first shutdown",
    ]
    with pytest.raises(RuntimeError, match="not started"):
        _ = lifespan.task_group


def test_explicit_plugins_load_in_fresh_interpreter(tmp_path):
    source_path = Path(config.__file__).resolve().parents[2]
    plugin_path = tmp_path / "isolated_plugin.py"
    plugin_path.write_text(
        "from opsmesh.framework import on_notice\nnotice = on_notice()\n",
        encoding="utf-8",
    )
    environment = dict(
        os.environ,
        PYTHONPATH=os.pathsep.join((str(source_path), str(tmp_path))),
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
from opsmesh import framework
plugin = framework.load_plugin('isolated_plugin')
assert plugin is not None
assert plugin.matcher == {plugin.module.notice}
assert plugin.module.notice.plugin is plugin
assert framework.get_plugin(plugin.id_) is plugin
assert framework.get_plugin_by_module_name('isolated_plugin.child') is plugin
assert plugin in framework.get_loaded_plugins()
from opsmesh.framework.matchers import matchers
assert plugin.module.notice in matchers[plugin.module.notice.priority]
from opsmesh.framework.dispatch import hooks
assert not hooks._event_preprocessors
""",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.anyio
async def test_shutdown_failure_still_drains_tasks_and_other_hooks():
    lifespan = _lifespan.Lifespan()
    events = []
    started = anyio.Event()

    async def background():
        try:
            started.set()
            await anyio.sleep_forever()
        finally:
            events.append("task stopped")

    async def failing():
        raise RuntimeError("shutdown failure")

    lifespan.on_shutdown(lambda: events.append("other hook"))
    lifespan.on_shutdown(failing)
    with pytest.raises(BaseExceptionGroup, match="Framework lifespan failed"):
        async with lifespan:
            lifespan.task_group.start_soon(background)
            await started.wait()
    assert events == ["other hook", "task stopped"]
    with pytest.raises(RuntimeError, match="not started"):
        _ = lifespan.task_group


@pytest.mark.anyio
async def test_startup_failure_unwinds_task_group():
    lifespan = _lifespan.Lifespan()
    events = []

    async def failing():
        raise RuntimeError("startup failure")

    lifespan.on_startup(failing)
    lifespan.on_shutdown(lambda: events.append("cleanup"))
    with pytest.raises(RuntimeError, match="startup failure"):
        await lifespan.startup()
    assert events == ["cleanup"]
    with pytest.raises(RuntimeError, match="not started"):
        _ = lifespan.task_group


@pytest.mark.anyio
async def test_host_cancellation_still_awaits_shutdown_hooks():
    lifespan = _lifespan.Lifespan()
    events = []

    async def cleanup():
        await anyio.sleep(0)
        events.append("cleanup")

    lifespan.on_shutdown(cleanup)
    with anyio.CancelScope() as scope:
        async with lifespan:
            scope.cancel()
            await anyio.sleep(0)
    assert events == ["cleanup"]
    with pytest.raises(RuntimeError, match="not started"):
        _ = lifespan.task_group
