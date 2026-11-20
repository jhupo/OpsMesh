from contextlib import AsyncExitStack

import anyio
import pytest

from opsmesh import dependencies, params
from opsmesh.dependencies import params as internal_params
from opsmesh.utils import run_sync_ctx_manager


@pytest.mark.anyio
async def test_shared_generator_dependency_is_entered_and_closed_once():
    lifecycle = []

    async def resource():
        lifecycle.append("enter")
        try:
            await anyio.sleep(0)
            yield "value"
        finally:
            lifecycle.append("exit")

    async def handler(
        left: str = params.Depends(resource),
        right: str = params.Depends(resource),
    ):
        return left, right

    dependent = dependencies.Dependent.parse(
        call=handler, allow_types=(internal_params.DependParam,)
    )
    async with AsyncExitStack() as stack:
        result = await dependent(stack=stack, dependency_cache={})
    assert (result, lifecycle) == (("value", "value"), ["enter", "exit"])


@pytest.mark.anyio
async def test_generator_cleanup_runs_when_handler_fails():
    lifecycle = []

    async def resource():
        try:
            yield "value"
        finally:
            lifecycle.append("exit")

    async def handler(value: str = params.Depends(resource)):
        raise RuntimeError(value)

    dependent = dependencies.Dependent.parse(
        call=handler, allow_types=(internal_params.DependParam,)
    )
    with pytest.raises(RuntimeError, match="value"):
        async with AsyncExitStack() as stack:
            await dependent(stack=stack, dependency_cache={})
    assert lifecycle == ["exit"]


@pytest.mark.anyio
async def test_sync_context_cleanup_receives_cancellation():
    events = []

    class Resource:
        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, exc_type, error, traceback):
            events.append(exc_type)
            return False

    with anyio.CancelScope() as scope:
        async with run_sync_ctx_manager(Resource()):
            scope.cancel()
            await anyio.sleep(0)

    assert events == ["enter", anyio.get_cancelled_exc_class()]


@pytest.mark.anyio
async def test_driver_public_lifespan_exposes_hooks_and_state(runtime_type):
    from opsmesh.config import Config, Env

    Driver = runtime_type

    driver = Driver(Env(), Config())
    events = []
    driver.on_startup(lambda: events.append("startup"))
    driver.on_ready(lambda: events.append("ready"))
    driver.on_shutdown(lambda: events.append("shutdown"))
    assert not driver.started
    async with driver.lifespan():
        assert driver.started
        assert driver.task_group is not None
        events.append("running")
    assert not driver.started
    assert events == ["startup", "ready", "running", "shutdown"]
