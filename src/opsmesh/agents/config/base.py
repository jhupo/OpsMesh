"""Shared serializable values, separate from live SDK objects."""

from typing import Annotated, Any, cast

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

Identifier = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]


class ConfigValue(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, validate_default=True, serialize_by_alias=True
    )

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        result = cast(dict[str, Any], handler(self))
        # Discriminators must survive exclude_unset, unlike optional SDK settings
        # where omission and an explicit null can carry different meanings.
        if "type" in type(self).model_fields:
            result["type"] = self.__dict__["type"]
        return result
