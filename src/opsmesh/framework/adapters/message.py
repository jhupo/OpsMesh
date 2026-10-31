"""Public protocol-neutral message and segment contracts."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class OpsMeshMessageSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    type: str
    data: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def text(cls, text: str) -> Self:
        return cls(type="text", data={"text": text})

    @classmethod
    def file(cls, resource_ref: str, *, name: str | None = None) -> Self:
        return cls(type="file", data={"resource_ref": resource_ref, "name": name})

    @classmethod
    def image(cls, resource_ref: str) -> Self:
        return cls(type="image", data={"resource_ref": resource_ref})

    def __str__(self) -> str:
        return str(self.data.get("text", "")) if self.type == "text" else f"[{self.type}]"


class OpsMeshMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    segments: tuple[OpsMeshMessageSegment, ...] = ()

    def __init__(
        self,
        value: str | OpsMeshMessageSegment | Iterable[OpsMeshMessageSegment] | None = None,
        **data: Any,
    ) -> None:
        if value is not None:
            if "segments" in data:
                raise TypeError("Specify either value or segments")
            if isinstance(value, str):
                data["segments"] = (OpsMeshMessageSegment.text(value),)
            elif isinstance(value, OpsMeshMessageSegment):
                data["segments"] = (value,)
            else:
                data["segments"] = tuple(value)
        super().__init__(**data)

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"segments": [{"type": "text", "data": {"text": value}}]}
        if isinstance(value, (tuple, list)):
            return {"segments": value}
        return value

    def __iter__(self) -> Iterator[OpsMeshMessageSegment]:  # type: ignore[override]
        return iter(self.segments)

    def __len__(self) -> int:
        return len(self.segments)

    def __str__(self) -> str:
        return "".join(map(str, self.segments))

    def extract_plain_text(self) -> str:
        return "".join(str(s) for s in self.segments if s.type == "text")

    def __add__(self, other: str | OpsMeshMessageSegment | OpsMeshMessage) -> OpsMeshMessage:
        addition = other if isinstance(other, OpsMeshMessage) else OpsMeshMessage(other)
        return OpsMeshMessage(self.segments + addition.segments)
