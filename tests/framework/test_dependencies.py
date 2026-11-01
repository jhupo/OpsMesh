import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Annotated

import pytest

from opsmesh.framework.dependencies import CallableDependency, DependencyScope, Depends
from opsmesh.framework.exception import DependencyError


@pytest.mark.asyncio
async def test_nested_annotated_dependencies_cache_and_cleanup() -> None:
    observations: list[str] = []

    async def resource() -> AsyncIterator[int]:
        observations.append("open")
        try:
            yield 4
        finally:
            observations.append("close")

    async def derived(value: int = Depends(resource)) -> int:
        return value + 1

    async def handler(a: Annotated[int, Depends(derived)], b: int = Depends(derived)) -> int:
        return a + b

    async with DependencyScope() as scope:
        assert await scope.invoke(handler) == 10
        assert observations == ["open"]
    assert observations == ["open", "close"]


@pytest.mark.asyncio
async def test_shared_cache_has_one_concurrent_producer_and_no_cross_scope_leak() -> None:
    opened = 0

    async def provider() -> object:
        nonlocal opened
        opened += 1
        await asyncio.sleep(0)
        return object()

    async def handler(value: object = Depends(provider)) -> object:
        return value

    async with DependencyScope() as scope:
        first, second = await asyncio.gather(scope.invoke(handler), scope.fork().invoke(handler))
        assert first is second
    async with DependencyScope() as scope:
        assert await scope.invoke(handler) is not first
    assert opened == 2


@pytest.mark.asyncio
async def test_uncached_generator_dependencies_release_in_reverse_order_on_error() -> None:
    changes: list[str] = []

    def resource() -> Iterator[int]:
        number = len(changes)
        changes.append(f"open:{number}")
        try:
            yield number
        finally:
            changes.append(f"close:{number}")

    async def handler(
        a: int = Depends(resource, use_cache=False), b: int = Depends(resource, use_cache=False)
    ) -> None:
        assert a != b
        raise ValueError("handler failure")

    with pytest.raises(ValueError, match="handler failure"):
        async with DependencyScope() as scope:
            await scope.invoke(handler)
    assert changes == ["open:0", "open:1", "close:1", "close:0"]


def test_dependency_cycle_is_rejected_before_execution() -> None:
    def first(value: object = None) -> object:
        return value

    def second(value: object = Depends(first)) -> object:
        return value

    first.__defaults__ = (Depends(second),)
    with pytest.raises(DependencyError, match="Dependency cycle"):
        CallableDependency.parse(first)


@pytest.mark.asyncio
async def test_cancelled_handler_releases_resources() -> None:
    opened = asyncio.Event()
    released = asyncio.Event()

    async def resource() -> AsyncIterator[str]:
        try:
            yield "resource"
        finally:
            released.set()

    async def handler(value: str = Depends(resource)) -> None:
        assert value == "resource"
        opened.set()
        await asyncio.Event().wait()

    async def run() -> None:
        async with DependencyScope() as scope:
            await scope.invoke(handler)

    task = asyncio.create_task(run())
    await asyncio.wait_for(opened.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert released.is_set()
