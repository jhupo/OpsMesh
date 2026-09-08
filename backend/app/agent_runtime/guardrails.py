from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from backend.app.agent_runtime.contracts import (
    AgentRuntimeEvent,
    AgentRuntimeGuardrail,
    AgentRuntimeGuardrailResult,
    AgentRuntimeGuardrails,
    AgentRuntimeOutputSchema,
    AgentRuntimeStructuredOutput,
)
from backend.app.agent_runtime.errors import (
    AgentRuntimeGuardrailBlockedError,
    AgentRuntimeOutputValidationError,
)
from backend.app.security.redaction import redact_sensitive_payload

MAX_GUARDRAILS_PER_STAGE = 20
MAX_BLOCKED_TERMS = 100
MAX_TERM_LENGTH = 256
MAX_CHARACTER_LIMIT = 1_000_000
_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_SUPPORTED_GUARDRAIL_KINDS = frozenset(
    {"blocked_terms", "max_characters", "json_schema"}
)


def runtime_controls_snapshot(
    *,
    model_settings: object,
    runtime_policy: object,
) -> dict[str, object]:
    settings = _mapping(model_settings, "Agent model settings")
    policy = _mapping(runtime_policy, "Agent runtime policy")
    output_schema = parse_output_schema(settings.get("output_schema"))
    guardrails = parse_guardrails(policy.get("guardrails"))
    return {
        "output_schema": asdict(output_schema) if output_schema is not None else None,
        "guardrails": guardrails_snapshot(guardrails),
    }


def runtime_controls_from_snapshot(
    snapshot: dict[str, object],
) -> tuple[AgentRuntimeOutputSchema | None, AgentRuntimeGuardrails | None]:
    return (
        parse_output_schema(snapshot.get("output_schema")),
        parse_guardrails(snapshot.get("guardrails")),
    )


def parse_output_schema(value: object) -> AgentRuntimeOutputSchema | None:
    if value is None:
        return None
    raw = _mapping(value, "Agent output schema")
    name = _name(raw.get("name"), "Agent output schema name")
    schema = _mapping(raw.get("schema"), "Agent output JSON schema")
    _validate_json_schema(schema, "Agent output JSON schema")
    strict = raw.get("strict", True)
    if not isinstance(strict, bool):
        raise ValueError("Agent output schema strict must be a boolean")
    version = raw.get("version")
    if version is not None and (
        not isinstance(version, str) or not version or len(version) > 64
    ):
        raise ValueError("Agent output schema version must be a non-empty short string")
    return AgentRuntimeOutputSchema(
        name=name,
        schema=dict(schema),
        strict=strict,
        version=version,
    )


def parse_guardrails(value: object) -> AgentRuntimeGuardrails | None:
    if value is None:
        return None
    raw = _mapping(value, "Agent guardrails")
    unknown = set(raw) - {"input", "output"}
    if unknown:
        raise ValueError(f"Unknown agent guardrail stages: {', '.join(sorted(unknown))}")
    input_guardrails = _parse_guardrail_stage(raw.get("input", []), "input")
    output_guardrails = _parse_guardrail_stage(raw.get("output", []), "output")
    return AgentRuntimeGuardrails(input=input_guardrails, output=output_guardrails)


def guardrails_snapshot(value: AgentRuntimeGuardrails | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "input": [asdict(item) for item in value.input],
        "output": [asdict(item) for item in value.output],
    }


def evaluate_guardrail(
    definition: AgentRuntimeGuardrail,
    value: object,
    *,
    stage: str,
) -> AgentRuntimeGuardrailResult:
    if stage not in {"input", "output"}:
        raise ValueError("Agent guardrail stage must be input or output")
    passed, evidence = _evaluate(definition, value)
    status = "passed" if passed else "blocked" if definition.blocking else "flagged"
    return AgentRuntimeGuardrailResult(
        stage=stage,
        name=definition.name,
        kind=definition.kind,
        status=status,
        blocking=definition.blocking,
        evidence=redact_sensitive_payload(evidence),
    )


def evaluate_guardrail_stage(
    definitions: tuple[AgentRuntimeGuardrail, ...],
    value: object,
    *,
    stage: str,
    results: list[AgentRuntimeGuardrailResult],
) -> None:
    for definition in definitions:
        result = evaluate_guardrail(definition, value, stage=stage)
        results.append(result)
        if result.status == "blocked":
            raise AgentRuntimeGuardrailBlockedError(result)


def validated_structured_output(
    schema: AgentRuntimeOutputSchema,
    value: object,
) -> AgentRuntimeStructuredOutput:
    normalized = _structured_value(schema, value)
    validator = Draft202012Validator(schema.schema)
    error = next(validator.iter_errors(normalized), None)
    if error is not None:
        raise AgentRuntimeOutputValidationError(
            schema_name=schema.name,
            schema_version=schema.version,
            validator=str(error.validator or "schema"),
        )
    return AgentRuntimeStructuredOutput(
        value=normalized,
        schema_name=schema.name,
        schema_version=schema.version,
        validated=True,
    )


def guardrail_events(
    results: list[AgentRuntimeGuardrailResult],
) -> list[AgentRuntimeEvent]:
    return [
        AgentRuntimeEvent(
            event_type=f"agent.guardrail.{result.status}",
            message=f"Agent {result.stage} guardrail {result.status}.",
            payload=redact_sensitive_payload(asdict(result)),
        )
        for result in results
    ]


def _parse_guardrail_stage(
    value: object,
    stage: str,
) -> tuple[AgentRuntimeGuardrail, ...]:
    if not isinstance(value, list):
        raise ValueError(f"Agent {stage} guardrails must be a list")
    if len(value) > MAX_GUARDRAILS_PER_STAGE:
        raise ValueError(
            f"Agent {stage} guardrails exceed the {MAX_GUARDRAILS_PER_STAGE} item limit"
        )
    parsed: list[AgentRuntimeGuardrail] = []
    names: set[str] = set()
    for raw_item in value:
        item = _mapping(raw_item, f"Agent {stage} guardrail")
        name = _name(item.get("name"), f"Agent {stage} guardrail name")
        if name in names:
            raise ValueError(f"Agent {stage} guardrail names must be unique")
        names.add(name)
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in _SUPPORTED_GUARDRAIL_KINDS:
            raise ValueError(f"Agent {stage} guardrail {name} has an unsupported kind")
        config = _mapping(item.get("config", {}), f"Agent {stage} guardrail config")
        _validate_guardrail_config(kind, config)
        blocking = item.get("blocking", True)
        if not isinstance(blocking, bool):
            raise ValueError(f"Agent {stage} guardrail {name} blocking must be a boolean")
        parsed.append(
            AgentRuntimeGuardrail(
                name=name,
                kind=kind,
                config=dict(config),
                blocking=blocking,
            )
        )
    return tuple(parsed)


def _validate_guardrail_config(kind: str, config: dict[str, object]) -> None:
    if kind == "blocked_terms":
        unknown = set(config) - {"terms", "case_sensitive"}
        terms = config.get("terms")
        if unknown or not isinstance(terms, list) or not 1 <= len(terms) <= MAX_BLOCKED_TERMS:
            raise ValueError("blocked_terms guardrail requires a bounded terms list")
        if any(
            not isinstance(term, str) or not term or len(term) > MAX_TERM_LENGTH
            for term in terms
        ):
            raise ValueError("blocked_terms guardrail terms must be non-empty short strings")
        case_sensitive = config.get("case_sensitive", False)
        if not isinstance(case_sensitive, bool):
            raise ValueError("blocked_terms case_sensitive must be a boolean")
        return
    if kind == "max_characters":
        if set(config) != {"max_characters"}:
            raise ValueError("max_characters guardrail requires only max_characters")
        maximum = config.get("max_characters")
        if (
            not isinstance(maximum, int)
            or isinstance(maximum, bool)
            or not 1 <= maximum <= MAX_CHARACTER_LIMIT
        ):
            raise ValueError("max_characters must be a positive bounded integer")
        return
    if set(config) != {"schema"}:
        raise ValueError("json_schema guardrail requires only schema")
    schema = _mapping(config.get("schema"), "Guardrail JSON schema")
    _validate_json_schema(schema, "Guardrail JSON schema")


def _evaluate(
    definition: AgentRuntimeGuardrail,
    value: object,
) -> tuple[bool, dict[str, object]]:
    if definition.kind == "blocked_terms":
        text = _text_value(value)
        terms = definition.config["terms"]
        case_sensitive = bool(definition.config.get("case_sensitive", False))
        haystack = text if case_sensitive else text.casefold()
        matches = sum(
            1
            for raw_term in terms
            if isinstance(raw_term, str)
            and (raw_term if case_sensitive else raw_term.casefold()) in haystack
        )
        return matches == 0, {
            "evaluated_characters": len(text),
            "match_count": matches,
        }
    if definition.kind == "max_characters":
        text = _text_value(value)
        maximum = int(definition.config["max_characters"])
        return len(text) <= maximum, {
            "evaluated_characters": len(text),
            "max_characters": maximum,
        }
    schema = _mapping(definition.config["schema"], "Guardrail JSON schema")
    error = next(Draft202012Validator(schema).iter_errors(value), None)
    return error is None, {
        "validator": str(error.validator or "schema") if error is not None else None,
    }


def _structured_value(schema: AgentRuntimeOutputSchema, value: object) -> object:
    if isinstance(value, str) and schema.schema.get("type") != "string":
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise AgentRuntimeOutputValidationError(
                schema_name=schema.name,
                schema_version=schema.version,
                validator="json",
            ) from exc
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


def _text_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _validate_json_schema(schema: dict[str, object], label: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"{label} is invalid: {exc.message}") from exc


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return dict(value)


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not _NAME_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must be a stable identifier")
    return value
