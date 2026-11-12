"""Pydantic v2 validation helpers for framework dependency descriptors."""

from collections.abc import Callable, Generator
from dataclasses import dataclass, is_dataclass
from functools import cached_property, wraps
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    ParamSpec,
    Protocol,
    Self,
    TypeVar,
    get_args,
    get_origin,
)

from pydantic import BaseModel, Field, GetCoreSchemaHandler
from pydantic import ConfigDict as ConfigDict
from pydantic import TypeAdapter as TypeAdapter
from pydantic import field_validator as field_validator
from pydantic import model_validator as model_validator
from pydantic._internal._repr import display_as_type
from pydantic.fields import FieldInfo as BaseFieldInfo
from pydantic_core import CoreSchema, core_schema
from pydantic_core import PydanticUndefined as PydanticUndefined
from pydantic_core import PydanticUndefinedType as PydanticUndefinedType
from typing_extensions import is_typeddict

from opsmesh.typing import origin_is_annotated

T = TypeVar("T")
P = ParamSpec("P")


if TYPE_CHECKING:

    class _CustomValidationClass(Protocol):
        @classmethod
        def __get_validators__(
            cls,
        ) -> Generator[Callable[..., Any], None, None]: ...

    CVC = TypeVar("CVC", bound=_CustomValidationClass)


__all__ = (
    "DEFAULT_CONFIG",
    "ConfigDict",
    "FieldInfo",
    "LegacyUnionField",
    "ModelField",
    "PydanticUndefined",
    "PydanticUndefinedType",
    "Required",
    "TypeAdapter",
    "custom_validation",
    "field_validator",
    "model_fields",
    "model_validator",
)

__autodoc__ = {
    "PydanticUndefined": "Pydantic Undefined object",
    "PydanticUndefinedType": "Pydantic Undefined type",
}


Required = Ellipsis
"""Required-value marker used by framework dependency descriptors."""


DEFAULT_CONFIG = ConfigDict(extra="allow", arbitrary_types_allowed=True)
"""Default config for validations."""


def _get_legacy_union_field(func: Callable[P, T]) -> Callable[P, T]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        kwargs["union_mode"] = "left_to_right"
        return func(*args, **kwargs)

    return wrapper


LegacyUnionField = _get_legacy_union_field(Field)
LegacyUnionField.__doc__ = "Mark field to use legacy left to right union mode"


class FieldInfo(BaseFieldInfo):  # pyright: ignore[reportGeneralTypeIssues]
    """Field metadata with positional defaults and descriptor extensions."""

    # make default can be positional argument
    def __init__(self, default: Any = PydanticUndefined, **kwargs: Any) -> None:
        super().__init__(default=default, **kwargs)

    @property
    def extra(self) -> dict[str, Any]:
        """Descriptor attributes outside the standard Pydantic fields."""
        # extract extra data from attributes set except used slots
        # we need to call super in advance due to
        # comprehension not inlined in cpython < 3.12
        # https://peps.python.org/pep-0709/
        slots = super().__slots__
        return {k: v for k, v in self._attributes_set.items() if k not in slots}

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
    """Validated dependency field backed by a cached Pydantic TypeAdapter."""

    name: str
    """The name of the field."""
    annotation: Any
    """The annotation of the field."""
    field_info: FieldInfo
    """The FieldInfo of the field."""

    @classmethod
    def _construct(
        cls, name: str, annotation: Any, field_info: FieldInfo
    ) -> Self:
        return cls(name, annotation, field_info)

    @classmethod
    def construct(
        cls, name: str, annotation: Any, field_info: FieldInfo | None = None
    ) -> Self:
        """Construct a ModelField from given infos."""
        return cls._construct(name, annotation, field_info or FieldInfo())

    def __hash__(self) -> int:
        # Each ModelField is unique for our purposes,
        # to allow store them in a set.
        return id(self)

    @cached_property
    def type_adapter(self) -> TypeAdapter:
        """TypeAdapter of the field.

        Cache the TypeAdapter to avoid creating it multiple times.
        Pydantic v2 uses too much cpu time to create TypeAdapter.

        See: https://github.com/pydantic/pydantic/issues/9834
        """
        return TypeAdapter(
            Annotated[self.annotation, self.field_info],
            config=None if self._annotation_has_config() else DEFAULT_CONFIG,
        )

    def _annotation_has_config(self) -> bool:
        """Check if the annotation has config.

        TypeAdapter raise error when annotation has config
        and given config is not None.
        """
        type_is_annotated = origin_is_annotated(get_origin(self.annotation))
        inner_type = (
            get_args(self.annotation)[0]
            if type_is_annotated
            else self.annotation
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
        """Get the default value of the field."""
        return self.field_info.get_default(call_default_factory=True)

    def _type_display(self):
        """Get the display of the type of the field."""
        return display_as_type(self.annotation)

    def validate_value(self, value: Any) -> Any:
        """Validate the value pass to the field."""
        return self.type_adapter.validate_python(value)


def model_fields(model: type[BaseModel]) -> list[ModelField]:
    """Get field list of a model."""
    return [
        ModelField._construct(
            name=name,
            annotation=field_info.rebuild_annotation(),
            field_info=FieldInfo._inherit_construct(field_info),
        )
        for name, field_info in model.model_fields.items()
    ]


def __get_pydantic_core_schema__(
    cls: type["_CustomValidationClass"],
    source_type: Any,
    handler: GetCoreSchemaHandler,
) -> CoreSchema:
    validators = list(cls.__get_validators__())
    if len(validators) == 1:
        return core_schema.no_info_plain_validator_function(validators[0])
    return core_schema.chain_schema(
        [
            core_schema.no_info_plain_validator_function(func)
            for func in validators
        ]
    )


def custom_validation(class_: type["CVC"]) -> type["CVC"]:
    """Adapt framework validator generators to Pydantic core schemas."""
    class_.__get_pydantic_core_schema__ = classmethod(
        __get_pydantic_core_schema__
    )
    return class_
