"""Offline bounded JSON Schema: validation must never perform network I/O."""

import json
from typing import Any

from jsonschema import Draft202012Validator

from opsmesh.shared.errors import InvalidArgumentError


def validate_schema(schema: dict[str, Any]) -> None:
    try:
        if len(json.dumps(schema, allow_nan=False).encode()) > 65536:
            raise ValueError("Schema byte bound")

        def resolve(reference: str) -> Any:
            value: Any = schema
            for part in reference[2:].split("/"):
                if not isinstance(value, dict):
                    raise ValueError("Invalid schema reference")
                value = value[part.replace("~1", "/").replace("~0", "~")]
            return value

        visited = 0

        def references(value: Any, chain: tuple[str, ...] = ()) -> None:
            nonlocal visited
            visited += 1
            if visited > 4096:
                raise ValueError("Schema resolution work bound")
            if isinstance(value, dict):
                reference = value.get("$ref")
                if reference is not None:
                    if reference in chain or len(chain) >= 16:
                        raise ValueError("Recursive schema resolution exceeds its bound")
                    references(resolve(reference), (*chain, reference))
                for key, item in value.items():
                    if key != "$ref":
                        references(item, chain)
            elif isinstance(value, list):
                for item in value:
                    references(item, chain)

        def walk(value: Any, depth: int = 0) -> None:
            if depth > 20:
                raise ValueError("Schema nesting bound")
            if isinstance(value, dict):
                if any(key in value for key in ("$dynamicRef", "$recursiveRef", "$id")):
                    raise ValueError("Dynamic schema resolution is forbidden")
                reference = value.get("$ref")
                if reference is not None and (
                    not isinstance(reference, str) or not reference.startswith("#/$defs/")
                ):
                    raise ValueError("Only local definition references are allowed")
                for item in value.values():
                    walk(item, depth + 1)
            elif isinstance(value, list):
                for item in value:
                    walk(item, depth + 1)

        walk(schema)
        references(schema)
        Draft202012Validator.check_schema(schema)
    except Exception:
        raise InvalidArgumentError(
            "The JSON Schema exceeds its bound or uses unsupported resolution"
        ) from None
