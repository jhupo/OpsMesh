"""Pydantic v2 field metadata and cached TypeAdapter wrapper for DI validation."""

from dataclasses import dataclass, is_dataclass
from functools import cached_property
from typing import Annotated, Any, Self, get_args, get_origin, is_typeddict

from pydantic import BaseModel, ConfigDict, TypeAdapter
from pydantic._internal._repr import display_as_type
from pydantic.fields import FieldInfo as BaseFieldInfo
from pydantic_core import PydanticUndefined

from opsmesh.typing import origin_is_annotated

DEFAULT_CONFIG = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class FieldInfo(BaseFieldInfo):  # pyright: ignore[reportGeneralTypeIssues]
    """FieldInfo allowing positional `default` construction."""

    def __init__(self, default: Any = PydanticUndefined, **kwargs: Any) -> None:
        super().__init__(default=default, **kwargs)

    @classmethod
    def _inherit_construct(
        cls, field_info: BaseFieldInfo | None = None, **kwargs: Any
    ) -> Self:
        init_kwargs = {}
        if field_info:
            init_kwargs.update(field_info._attributes_set)
        init_kwargs.update(kwargs)

        instance = cls(**init_kwargs)
        if field_info:
            instance.metadata = field_info.metadata
        return instance


@dataclass
class ModelField:
    """Resolved parameter/model field with cached Pydantic v2 TypeAdapter."""

    name: str
    annotation: Any
    field_info: FieldInfo

    @classmethod
    def construct(
        cls, name: str, annotation: Any, field_info: FieldInfo | None = None
    ) -> Self:
        return cls(name, annotation, field_info or FieldInfo())

    def __hash__(self) -> int:
        return id(self)

    @cached_property
    def type_adapter(self) -> TypeAdapter[Any]:
        return TypeAdapter(
            Annotated[self.annotation, self.field_info],
            config=None if self._annotation_has_config() else DEFAULT_CONFIG,
        )

    def _annotation_has_config(self) -> bool:
        type_is_annotated = origin_is_annotated(get_origin(self.annotation))
        inner_type = (
            get_args(self.annotation)[0] if type_is_annotated else self.annotation
        )
        try:
            return (
                issubclass(inner_type, BaseModel)
                or is_dataclass(inner_type)
                or is_typeddict(inner_type)
            )
        except TypeError:
            return False

    def get_default(self) -> Any:
        return self.field_info.get_default(call_default_factory=True)

    def _type_display(self) -> str:
        return display_as_type(self.annotation)

    def validate_value(self, value: Any) -> Any:
        return self.type_adapter.validate_python(value)


def model_fields(model: type[BaseModel]) -> list[ModelField]:
    """Return resolved `ModelField` list for a Pydantic v2 `BaseModel`."""
    return [
        ModelField(
            name=name,
            annotation=field_info.rebuild_annotation(),
            field_info=FieldInfo._inherit_construct(field_info),
        )
        for name, field_info in model.model_fields.items()
    ]
