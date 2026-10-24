from pathlib import Path
from typing import Any, Protocol


class Resources(Protocol):
    data_dir: Path
    workspace: Path | None

    async def model(
        self,
        connection_id: str | None = None,
        model_name: str | None = None,
        *,
        reserve_tokens: int = 8192,
    ) -> Any: ...

    async def model_provider(
        self, connections: dict[str, str], *, reserve_tokens: int = 8192
    ) -> Any: ...

    async def artifact(self, path: str, *, name: str | None = None) -> str: ...
