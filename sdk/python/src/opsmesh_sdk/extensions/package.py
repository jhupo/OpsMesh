"""Standard entry-point result; export schemas come from the executable declarations."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from opsmesh_sdk.extensions.hooks import HookDecision, HookInput, validate_decision
from opsmesh_sdk.extensions.operations import Operation
from opsmesh_sdk.manifest import HookExport, PackageManifest


@dataclass(frozen=True)
class Hook:
    export: HookExport
    handler: Callable[[HookInput], Awaitable[HookDecision]]

    async def invoke(self, argument: HookInput) -> HookDecision:
        if argument.stage not in self.export.stages:
            raise ValueError("The hook does not declare this stage")
        self.export.input_schema.validate_instance(argument.payload)
        decision = await self.handler(argument)
        return validate_decision(decision, patch_fields=self.export.patch_fields)


@dataclass(frozen=True)
class ExtensionExports:
    """Return from the ``opsmesh.extensions`` entry-point factory(context).

    The factory constructs declarations without I/O. During isolated discovery
    context is None. Invocation factories receive a real restricted context;
    handlers can capture it without any global authority or server imports.
    """

    metadata: PackageManifest
    operations: tuple[Operation, ...] = ()
    hooks: tuple[Hook, ...] = ()
    worker_startup: Callable[[], Awaitable[None]] | None = None
    worker_shutdown: Callable[[], Awaitable[None]] | None = None
    manifest: PackageManifest = field(init=False)

    def __post_init__(self) -> None:
        values = self.metadata.model_dump()
        values["operations"] = tuple(operation.export for operation in self.operations)
        values["hooks"] = tuple(hook.export for hook in self.hooks)
        object.__setattr__(self, "manifest", PackageManifest.model_validate(values))
