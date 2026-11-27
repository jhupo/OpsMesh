"""Compile-time dependency injection, caching, generator teardown, and typing."""

from collections.abc import AsyncGenerator, Generator
from contextlib import AsyncExitStack
from typing import Annotated

from pydantic import Field
import pytest

from opsmesh.dependencies import Dependent
from opsmesh.exception import TypeMisMatch
from opsmesh.internal.params import (
    DefaultParam,
    DependParam,
    Depends,
    EventParam,
    ExceptionParam,
    GatewayParam,
    MatcherParam,
    StateParam,
)
from opsmesh.typing import T_State

ALLOW = (
    DependParam,
    GatewayParam,
    EventParam,
    StateParam,
    MatcherParam,
    ExceptionParam,
    DefaultParam,
)


@pytest.mark.anyio
async def test_depends_caching_and_uncached_evaluation():
    counter = 0

    def next_id() -> int:
        nonlocal counter
        counter += 1
        return counter

    async def handler(
        a: int = Depends(next_id),
        b: int = Depends(next_id),
        c: int = Depends(next_id, use_cache=False),
    ) -> tuple[int, int, int]:
        return a, b, c

    dep = Dependent.parse(call=handler, allow_types=ALLOW)
    res = await dep(dependency_cache={})
    assert res[0] == res[1]
    assert res[2] != res[0]
    assert counter == 2


@pytest.mark.anyio
async def test_sync_and_async_generator_dependencies_cleanup():
    trace: list[str] = []

    def sync_res() -> Generator[str, None, None]:
        trace.append("sync:enter")
        try:
            yield "sync-val"
        finally:
            trace.append("sync:exit")

    async def async_res() -> AsyncGenerator[str, None]:
        trace.append("async:enter")
        try:
            yield "async-val"
        finally:
            trace.append("async:exit")

    async def handler(
        s: Annotated[str, Depends(sync_res)],
        a: Annotated[str, Depends(async_res)],
    ) -> str:
        trace.append(f"run:{s}:{a}")
        return f"{s}+{a}"

    dep = Dependent.parse(call=handler, allow_types=ALLOW)
    async with AsyncExitStack() as stack:
        out = await dep(stack=stack, dependency_cache={})
        assert out == "sync-val+async-val"
        assert "sync:exit" not in trace
        assert "async:exit" not in trace

    assert "sync:exit" in trace
    assert "async:exit" in trace
    assert trace.index("run:sync-val:async-val") < trace.index("sync:exit")
    assert trace.index("run:sync-val:async-val") < trace.index("async:exit")


@pytest.mark.anyio
async def test_depends_validate_default_param_and_parameterless():
    order: list[str] = []

    def guard() -> None:
        order.append("guard")

    def raw_num() -> str:
        return "42"

    def bad_num() -> str:
        return "not-an-int"

    async def ok_handler(
        val: int = Depends(raw_num, validate=Field(gt=10)),
        state: T_State = None,  # type: ignore[assignment]
        fallback: str = "default-ok",
    ) -> tuple[int, str]:
        order.append("handler")
        return val, fallback

    dep = Dependent.parse(
        call=ok_handler, parameterless=[Depends(guard)], allow_types=ALLOW
    )
    assert await dep(state={}, dependency_cache={}) == (42, "default-ok")
    assert order == ["guard", "handler"]

    async def fail_handler(val: int = Depends(bad_num, validate=True)) -> int:
        return val

    bad_dep = Dependent.parse(call=fail_handler, allow_types=ALLOW)
    with pytest.raises(ExceptionGroup) as exc_info:
        await bad_dep(dependency_cache={})
    assert any(isinstance(e, TypeMisMatch) for e in exc_info.value.exceptions)


def test_untyped_legacy_param_names_are_rejected():
    for name in ("gateway", "event", "state", "matcher", "exception"):
        fn = eval(f"lambda {name}: None")  # noqa: S307
        with pytest.raises(ValueError, match=f"Unknown parameter {name}"):
            Dependent.parse(call=fn, allow_types=ALLOW)
