"""SDK AsyncComputer bridge with owned per-run resources and explicit safety decisions."""

import inspect
from typing import Any

from agents import AsyncComputer, ComputerProvider, ComputerTool

from opsmesh.framework.agents import (
    AgentExecutionContext,
    ComputerAction,
    ComputerSession,
    ComputerToolBinding,
)
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.runtime.api import RuntimeExtensions

from .native_tools import authorize_native
from .serialization import serialize_sdk_value


class RegisteredComputer(AsyncComputer):
    def __init__(
        self,
        session: ComputerSession,
        binding: ComputerToolBinding,
        context: AgentExecutionContext,
        extensions: RuntimeExtensions,
    ) -> None:
        self.session, self.binding, self.context, self.extensions = (
            session,
            binding,
            context,
            extensions,
        )

    @property
    def environment(self) -> Any:
        return self.session.environment

    @property
    def dimensions(self) -> tuple[int, int]:
        return self.session.dimensions

    async def _perform(self, kind: str, **values: Any) -> str | None:
        self.extensions.computer(
            self.binding.computer_ref
        )  # Check plugin ownership on every action.
        await authorize_native(self.context, self.binding)
        return await self.session.perform(ComputerAction.model_validate({"type": kind, **values}))

    async def screenshot(self) -> str:
        image = await self._perform("screenshot")
        if not isinstance(image, str) or not image:
            raise OpsMeshError(
                "EXTENSION_INVALID", "Computer screenshots must contain base64 PNG data"
            )
        return image

    async def click(self, x: int, y: int, button: Any, *, keys: list[str] | None = None) -> None:
        await self._perform("click", x=x, y=y, button=button, keys=tuple(keys or ()))

    async def double_click(self, x: int, y: int, *, keys: list[str] | None = None) -> None:
        await self._perform("double_click", x=x, y=y, keys=tuple(keys or ()))

    async def scroll(
        self, x: int, y: int, scroll_x: int, scroll_y: int, *, keys: list[str] | None = None
    ) -> None:
        await self._perform(
            "scroll", x=x, y=y, scroll_x=scroll_x, scroll_y=scroll_y, keys=tuple(keys or ())
        )

    async def type(self, text: str) -> None:
        await self._perform("type", text=text)

    async def wait(self) -> None:
        await self._perform("wait")

    async def move(self, x: int, y: int) -> None:
        await self._perform("move", x=x, y=y)

    async def keypress(self, keys: list[str]) -> None:
        await self._perform("keypress", keys=tuple(keys))

    async def drag(self, path: list[tuple[int, int]], *, keys: list[str] | None = None) -> None:
        await self._perform("drag", path=tuple(path), keys=tuple(keys or ()))


def build_computer_tool(
    binding: ComputerToolBinding, extensions: RuntimeExtensions
) -> ComputerTool[Any]:
    async def create(*, run_context: Any) -> RegisteredComputer:
        context = run_context.context
        await authorize_native(context, binding)
        session = extensions.computer(binding.computer_ref)(context)
        session = await session if inspect.isawaitable(session) else session
        return RegisteredComputer(session, binding, context, extensions)

    async def dispose(*, run_context: Any, computer: RegisteredComputer) -> None:
        await computer.session.close()

    async def safety(data: Any) -> bool:
        if not binding.safety_check_ref:
            return False  # Safety checks need an explicit deployed policy.
        decision = extensions.computer_safety(binding.safety_check_ref)(
            data.ctx_wrapper.context,
            {
                "check": serialize_sdk_value(data.safety_check),
                "call": serialize_sdk_value(data.tool_call),
            },
        )
        decision = await decision if inspect.isawaitable(decision) else decision
        if not isinstance(decision, bool):
            raise OpsMeshError("EXTENSION_INVALID", "Computer safety policy must return bool")
        return decision

    return ComputerTool(
        computer=ComputerProvider(create=create, dispose=dispose), on_safety_check=safety
    )
