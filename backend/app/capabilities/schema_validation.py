from __future__ import annotations

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from backend.app.security.redaction import is_sensitive_payload_key, is_sensitive_payload_value

SAFE_REFERENCE_KEYS = frozenset(
    {
        "credential_reference_id",
        "mcp_credential_reference_id",
    }
)


def normalize_object_schema(schema: dict[str, object]) -> dict[str, object]:
    normalized = dict(schema)
    normalized.setdefault("type", "object")
    normalized.setdefault("properties", {})
    normalized.setdefault("additionalProperties", False)
    validate_json_schema(normalized)
    return normalized


def validate_json_schema(schema: dict[str, object]) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"Invalid JSON Schema: {exc.message}") from exc


def validate_parameters(
    parameters: dict[str, object],
    schema: dict[str, object],
    *,
    label: str,
) -> None:
    try:
        Draft202012Validator(schema).validate(parameters)
    except ValidationError as exc:
        path = ".".join(str(item) for item in exc.absolute_path)
        suffix = f" at {path}" if path else ""
        raise ValueError(f"Invalid {label}{suffix}: {exc.message}") from exc


def reject_embedded_secrets(value: object, *, path: str = "configuration") -> None:
    if isinstance(value, dict):
        for raw_key, item in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            if key not in SAFE_REFERENCE_KEYS and is_sensitive_payload_key(key):
                raise ValueError(
                    f"{child_path} must use a credential reference instead of a raw secret"
                )
            reject_embedded_secrets(item, path=child_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            reject_embedded_secrets(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and is_sensitive_payload_value(value):
        raise ValueError(f"{path} contains a secret-like value")
