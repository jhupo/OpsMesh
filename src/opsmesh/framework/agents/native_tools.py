"""Public local execution tool ports; no SDK objects cross this boundary."""

from typing import Annotated, Literal, Protocol

from pydantic import Field

from opsmesh.framework.typing import OpsMeshContract


class ShellToolBinding(OpsMeshContract):
    type: Literal["shell"] = "shell"
    executor_ref: str = Field(min_length=1)
    requires_approval: bool = True


class ApplyPatchToolBinding(OpsMeshContract):
    type: Literal["apply_patch"] = "apply_patch"
    editor_ref: str = Field(min_length=1)
    requires_approval: bool = True


class ComputerToolBinding(OpsMeshContract):
    type: Literal["computer"] = "computer"
    computer_ref: str = Field(min_length=1)
    safety_check_ref: str | None = None


NativeToolBinding = Annotated[
    ShellToolBinding | ApplyPatchToolBinding | ComputerToolBinding, Field(discriminator="type")
]


class ShellExecutionRequest(OpsMeshContract):
    call_id: str
    commands: tuple[str, ...]
    timeout_ms: int | None = Field(default=None, gt=0)
    max_output_length: int | None = Field(default=None, gt=0)


class ShellCommandResult(OpsMeshContract):
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False


class ShellExecutionResult(OpsMeshContract):
    outputs: tuple[ShellCommandResult, ...]


class FilePatchOperation(OpsMeshContract):
    type: Literal["create_file", "update_file", "delete_file"]
    path: str
    diff: str | None = None
    move_to: str | None = None


class FilePatchResult(OpsMeshContract):
    status: Literal["completed", "failed"]
    output: str | None = None


class ComputerAction(OpsMeshContract):
    type: Literal[
        "screenshot", "click", "double_click", "scroll", "type", "wait", "move", "keypress", "drag"
    ]
    x: int | None = None
    y: int | None = None
    button: Literal["left", "right", "wheel", "back", "forward"] | None = None
    scroll_x: int | None = None
    scroll_y: int | None = None
    text: str | None = None
    keys: tuple[str, ...] = ()
    path: tuple[tuple[int, int], ...] = ()


class ComputerSession(Protocol):
    environment: Literal["mac", "windows", "ubuntu", "browser"]
    dimensions: tuple[int, int]

    async def perform(self, action: ComputerAction) -> str | None: ...
    async def close(self) -> None: ...


class SessionCompactionConfiguration(OpsMeshContract):
    threshold: int = Field(default=10, ge=1)
    max_rollback_items: int = Field(default=10000, ge=1)
    mode: Literal["input", "auto", "previous_response_id"] = "input"
