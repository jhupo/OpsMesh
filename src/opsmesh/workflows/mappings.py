"""Bounded JSON mappings over explicit values; never attribute access or evaluation."""

import re
from copy import deepcopy

from opsmesh.shared.errors import InvalidArgumentError


class MissingReference(InvalidArgumentError):
    def __init__(self) -> None:
        super().__init__("A workflow mapping references a missing value")


def reference_path(value: object) -> list[str]:
    if not isinstance(value, str) or len(value) > 512:
        raise InvalidArgumentError("A workflow reference must be a bounded string")
    parts = value.split(".")
    if len(parts) > 32 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", p) for p in parts):
        raise InvalidArgumentError("Invalid workflow reference path")
    if parts[0] == "input":
        return parts
    if parts[0] != "steps" or len(parts) < 3 or parts[2] not in {"output", "status"}:
        raise InvalidArgumentError("References read input or steps.<key>.output/status")
    if parts[2] == "status" and len(parts) != 3:
        raise InvalidArgumentError("A step status has no child fields")
    return parts


def mapping_references(value: object, *, depth: int = 0) -> set[str]:
    if depth > 32:
        raise InvalidArgumentError("A workflow mapping exceeds the nesting limit")
    references: set[str] = set()
    if isinstance(value, dict):
        if "$ref" in value:
            if not set(value).issubset({"$ref", "default"}):
                raise InvalidArgumentError("A reference accepts only $ref and optional default")
            parts = reference_path(value["$ref"])
            if parts[0] == "steps":
                references.add(parts[1])
            if "default" in value:
                references |= mapping_references(value["default"], depth=depth + 1)
        else:
            for child in value.values():
                references |= mapping_references(child, depth=depth + 1)
    elif isinstance(value, list):
        for child in value:
            references |= mapping_references(child, depth=depth + 1)
    return references


def resolve_mapping(value: object, context: dict[str, object], *, depth: int = 0) -> object:
    if depth > 32:
        raise InvalidArgumentError("A workflow mapping exceeds the nesting limit")
    if isinstance(value, dict):
        if "$ref" in value:
            if not set(value).issubset({"$ref", "default"}):
                raise InvalidArgumentError("A reference accepts only $ref and optional default")
            current: object = context
            for part in reference_path(value["$ref"]):
                if isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isascii() and part.isdecimal():
                    index = int(part)
                    if index < len(current):
                        current = current[index]
                    else:
                        break
                else:
                    break
            else:
                return deepcopy(current)
            if "default" in value:
                return resolve_mapping(value["default"], context, depth=depth + 1)
            raise MissingReference()
        return {
            key: resolve_mapping(child, context, depth=depth + 1) for key, child in value.items()
        }
    if isinstance(value, list):
        return [resolve_mapping(child, context, depth=depth + 1) for child in value]
    return value
