"""Bounded JSON parameter materialization, with no I/O or authorization decisions."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from opsmesh_sdk.errors import ValidationError

PARAMETER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


@dataclass(frozen=True)
class ParameterLimits:
    max_depth: int = 32
    max_nodes: int = 16_384
    max_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in (self.max_depth, self.max_nodes, self.max_bytes)
        ):
            raise ValidationError("Parameter limits must be positive integers")


DEFAULT_LIMITS = ParameterLimits()


def normalize(value: Any, *, limits: ParameterLimits = DEFAULT_LIMITS) -> Any:
    """Copy a bounded JSON value, sort object keys and reject non-JSON values.

    The copy prevents later caller mutations changing an already materialized input.
    Repeated references are permitted; cyclic containers are not.
    """
    nodes = 0
    encoded_bytes = 0
    ancestors: set[int] = set()

    def account(size: int) -> None:
        nonlocal encoded_bytes
        encoded_bytes += size
        if encoded_bytes > limits.max_bytes:
            raise ValidationError("Capability parameters exceed the byte limit")

    def walk(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if depth > limits.max_depth or nodes > limits.max_nodes:
            raise ValidationError("Capability parameters exceed the structural limit")
        if isinstance(item, (dict, list)):
            if nodes + len(item) > limits.max_nodes:
                raise ValidationError("Capability parameters exceed the structural limit")
            identity = id(item)
            if identity in ancestors:
                raise ValidationError("Capability parameters contain a cyclic container")
            ancestors.add(identity)
            account(2 + max(0, len(item) - 1))
            try:
                if isinstance(item, list):
                    return [walk(child, depth + 1) for child in item]
                if any(not isinstance(key, str) for key in item):
                    raise ValidationError("Capability parameter object keys must be strings")
                result = {}
                for key in sorted(item):
                    if len(key) > limits.max_bytes:
                        raise ValidationError("Capability parameters exceed the byte limit")
                    try:
                        key.encode("utf-8")
                    except UnicodeError as exc:
                        raise ValidationError(
                            "Capability parameter keys must be Unicode text"
                        ) from exc
                    account(len(json.dumps(key, ensure_ascii=True).encode("ascii")) + 1)
                    result[key] = walk(item[key], depth + 1)
                return result
            finally:
                ancestors.remove(identity)
        if item is None or isinstance(item, (str, bool, int, float)):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValidationError("Capability parameters require finite JSON numbers")
            try:
                if isinstance(item, str):
                    if len(item) > limits.max_bytes:
                        raise ValidationError("Capability parameters exceed the byte limit")
                    item.encode("utf-8")
                account(len(json.dumps(item, ensure_ascii=True, allow_nan=False).encode("ascii")))
            except (ValueError, UnicodeError) as exc:
                raise ValidationError("Capability parameters must be valid JSON") from exc
            return item
        raise ValidationError("Capability parameters must contain only JSON values")

    return walk(value, 0)


def render(
    value: Any,
    parameters: dict[str, Any],
    *,
    limits: ParameterLimits = DEFAULT_LIMITS,
) -> Any:
    """Substitute values once; keys and replacement strings are never interpreted.

    A whole ``${name}`` preserves the parameter's JSON type. Embedded references
    require scalars and retain the original Python scalar string representation.
    Both inputs and expanded output obey the same resource limits.
    """
    declaration = normalize(value, limits=limits)
    arguments = normalize(parameters, limits=limits)
    if not isinstance(arguments, dict):
        raise ValidationError("Capability parameters must be a JSON object")
    expanded_bytes = 0

    def account_expansion(replacement: Any) -> Any:
        nonlocal expanded_bytes
        expanded_bytes += len(
            json.dumps(replacement, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        )
        if expanded_bytes > limits.max_bytes:
            raise ValidationError("Expanded capability parameters exceed the byte limit")
        return replacement

    def lookup(name: str) -> Any:
        if name not in arguments:
            raise ValidationError("A required capability parameter is missing")
        return arguments[name]

    def walk(item: Any) -> Any:
        if isinstance(item, str):
            match = PARAMETER.fullmatch(item)
            if match is not None:
                return account_expansion(lookup(match[1]))

            def replace(found: re.Match[str]) -> str:
                replacement = lookup(found[1])
                if isinstance(replacement, (dict, list)):
                    raise ValidationError("An embedded parameter must be scalar")
                return str(replacement)

            # Bound expansion before constructing an amplified string.
            size = len(item.encode("utf-8"))
            for found in PARAMETER.finditer(item):
                replacement = replace(found)
                size += len(replacement.encode("utf-8")) - len(found[0])
                if size > limits.max_bytes:
                    raise ValidationError("Expanded capability parameters exceed the byte limit")
            return account_expansion(PARAMETER.sub(replace, item))
        if isinstance(item, list):
            return [walk(child) for child in item]
        if isinstance(item, dict):
            return {key: walk(child) for key, child in item.items()}
        return item

    return normalize(walk(declaration), limits=limits)
