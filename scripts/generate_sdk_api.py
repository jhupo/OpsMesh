"""Generate the SDK business API groups from the platform OpenAPI contract.

The generator reads ``docs/openapi.json`` (produced by
``scripts/export_api_docs.py``) and ``sdk/python/codegen.toml`` (the sole
mapping convention) and writes ``sdk/python/src/opsmesh_sdk/api/<group>.py``.
Every operation carries a stable dotted operation id whose first segment names
the group; the remaining segments name the sub-client chain and the method.
Run with ``--check`` to fail when the committed generated files differ from
the current contract.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CODEGEN_PATH = ROOT / "sdk/python/codegen.toml"
SDK_ROOT = ROOT / "sdk/python"
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete"})
SUCCESS_CODES = ("200", "201", "202")
MAX_LINE = 100
REQUEST_INDENT = 12


class GenerationError(Exception):
    """The OpenAPI contract or the codegen mapping cannot produce a client."""


@dataclass(frozen=True)
class ClientGroup:
    group: str
    target: str


@dataclass(frozen=True)
class CodegenConfig:
    openapi_source: Path
    default_prefix: str
    error_model: str
    clients: tuple[ClientGroup, ...]
    shared_values: Mapping[str, str]
    binary: Mapping[str, str]

    def target_of(self, group: str) -> Path:
        for client in self.clients:
            if client.group == group:
                return SDK_ROOT / "src/opsmesh_sdk" / client.target
        raise GenerationError(f"the {group!r} group is not declared in codegen.toml")


@dataclass(frozen=True)
class Parameter:
    name: str
    annotation: str
    default: str | None


@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str
    path: str
    description: str
    path_params: tuple[Parameter, ...]
    query_params: tuple[Parameter, ...]
    body_model: str | None
    body_dump: str
    response_kind: str
    response_model: str | None
    binary_direction: str | None

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(self.operation_id.split("."))

    @property
    def group(self) -> str:
        return self.segments[0]

    @property
    def chain(self) -> tuple[str, ...]:
        return self.segments[1:-1]

    @property
    def method_name(self) -> str:
        return self.segments[-1]

@dataclass
class SubClient:
    name: str
    prefix: str
    class_name: str
    children: tuple[SubClient, ...]
    operations: tuple[Operation, ...]


class Writer:
    """Accumulates rendered lines; ``blank`` always emits one empty line."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def line(self, text: str = "") -> None:
        self.lines.append(text)

    def extend(self, texts: Iterable[str]) -> None:
        self.lines.extend(texts)

    def blank(self) -> None:
        self.lines.append("")

    def render(self) -> str:
        return "\n".join(self.lines).rstrip("\n") + "\n"


def load_config() -> CodegenConfig:
    raw = tomllib.loads(CODEGEN_PATH.read_text(encoding="utf-8"))
    openapi = raw.get("openapi", {})
    clients = tuple(
        ClientGroup(group=entry["group"], target=entry["target"])
        for entry in raw.get("clients", [])
    )
    binary = {entry["operation"]: entry["direction"] for entry in raw.get("binary", [])}
    return CodegenConfig(
        openapi_source=ROOT / openapi.get("source", "docs/openapi.json"),
        default_prefix=openapi.get("default_prefix", "/api/v1"),
        error_model=openapi.get("error_model", "ErrorResponse"),
        clients=clients,
        shared_values=dict(raw.get("shared_values", {})),
        binary=binary,
    )


def load_document(config: CodegenConfig) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(config.openapi_source.read_text(encoding="utf-8"))
    return document


def ref_name(reference: str) -> str:
    return reference.rsplit("/", 1)[-1]


def _relative_path(path: str, prefix: str, *, where: str) -> str:
    """Strip the exported API prefix so the path stays relative.

    The generated clients prepend the runtime API root of their channel, so a
    server that serves another ``OPSMESH_API_PREFIX`` is reachable without
    regenerating. A path outside the declared prefix means the export and the
    codegen configuration disagree, which is a generation error rather than a
    silently wrong URL.
    """
    if not path.startswith(prefix):
        raise GenerationError(
            f"{where}: {path!r} is outside the declared API prefix {prefix!r}"
        )
    relative = path[len(prefix) :]
    return relative if relative.startswith("/") else f"/{relative}"


def python_type(schema: Mapping[str, Any], shared: Mapping[str, str], *, where: str) -> str:
    """Render one JSON schema as a Python annotation."""
    if "$ref" in schema:
        name = ref_name(schema["$ref"])
        return shared.get(name, name)
    variants = schema.get("anyOf")
    if variants is not None:
        nullable = any(variant.get("type") == "null" for variant in variants)
        rendered = [
            python_type(variant, shared, where=where)
            for variant in variants
            if variant.get("type") != "null"
        ]
        joined = " | ".join(dict.fromkeys(rendered)) if rendered else "object"
        return f"{joined} | None" if nullable else joined
    kind = schema.get("type")
    if kind == "array":
        return f"list[{python_type(schema.get('items', {}), shared, where=where)}]"
    if kind == "integer":
        return "int"
    if kind == "number":
        return "float"
    if kind == "boolean":
        return "bool"
    if kind == "string":
        media_format = schema.get("format")
        if media_format == "uuid":
            return "UUID"
        if media_format == "date-time":
            return "datetime"
        return "str"
    if kind == "object":
        if "properties" in schema:
            raise GenerationError(f"{where}: inline object schemas must become component models")
        return "dict[str, object]"
    raise GenerationError(f"{where}: unsupported schema {dict(schema)}")


def render_default(value: Any) -> str:
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int | float | str | list):
        return repr(value)
    if isinstance(value, dict):
        # An empty object is the only meaningful schema default; a non-empty
        # one would need a canonical literal the schema cannot guarantee.
        if value:
            raise GenerationError(f"unsupported default value {value!r}")
        return "{}"
    raise GenerationError(f"unsupported default value {value!r}")


def build_parameter(
    raw: Mapping[str, Any], shared: Mapping[str, str], *, where: str
) -> Parameter:
    annotation = python_type(raw["schema"], shared, where=f"{where} parameter {raw['name']}")
    if raw.get("required", False):
        return Parameter(name=raw["name"], annotation=annotation, default=None)
    default = raw["schema"].get("default")
    return Parameter(name=raw["name"], annotation=annotation, default=render_default(default))


def json_schema_of(container: Mapping[str, Any]) -> Mapping[str, Any] | None:
    media = container.get("content", {}).get("application/json")
    if media is None:
        return None
    schema: Mapping[str, Any] = media["schema"]
    return schema


def response_shape(
    operation: Mapping[str, Any],
    where: str,
    shared: Mapping[str, str],
    binary_direction: str | None,
) -> tuple[str, str | None]:
    if binary_direction == "response":
        return "binary", None
    responses = operation.get("responses", {})
    success = next((code for code in SUCCESS_CODES if code in responses), None)
    if success is None:
        return "none", None
    schema = json_schema_of(responses[success])
    if schema is None:
        raise GenerationError(
            f"{where}: the success response is not application/json; "
            "declare the operation in codegen [[binary]]"
        )
    if "$ref" in schema:
        name = ref_name(schema["$ref"])
        return "model", shared.get(name, name)
    if schema.get("type") == "array":
        item = schema.get("items", {})
        if "$ref" not in item:
            raise GenerationError(f"{where}: the response array items are not a component model")
        name = ref_name(item["$ref"])
        return "array", shared.get(name, name)
    raise GenerationError(
        f"{where}: the success response is neither a component model nor an array of one"
    )


def is_nullable(schema: Mapping[str, Any]) -> bool:
    variants = schema.get("anyOf")
    return isinstance(variants, list) and any(
        variant.get("type") == "null" for variant in variants
    )


def body_dump_expression(
    model: str, schemas: Mapping[str, Any], *, where: str
) -> str:
    """Serialize one request body: optional fields are omitted, never nulled.

    An absent optional field takes the server-side default, while an explicit
    null reaches a field whose schema forbids it, so the dump drops unset
    optional values. A model with a required nullable field cannot drop every
    null; it excludes exactly its optional fields instead.
    """
    schema = schemas.get(model)
    if schema is None:
        raise GenerationError(f"{where}: the request model {model!r} is not defined")
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    optional = [name for name in properties if name not in required]
    if not optional:
        return 'request.model_dump(mode="json")'
    if any(is_nullable(properties[name]) for name in required):
        excluded = ", ".join(repr(name) for name in optional)
        return f'request.model_dump(mode="json", exclude={{{excluded}}})'
    return 'request.model_dump(mode="json", exclude_none=True)'


def collect_operations(document: Mapping[str, Any], config: CodegenConfig) -> list[Operation]:
    shared = config.shared_values
    schemas = document["components"]["schemas"]
    operations: list[Operation] = []
    seen: set[str] = set()
    for path, item in sorted(document["paths"].items()):
        for method, operation in sorted(item.items()):
            if method not in HTTP_METHODS:
                continue
            operation_id = operation.get("operationId")
            if not isinstance(operation_id, str) or not operation_id:
                raise GenerationError(f"{method.upper()} {path} has no operation id")
            segments = operation_id.split(".")
            if len(segments) < 2 or not all(segment.isidentifier() for segment in segments):
                raise GenerationError(f"{operation_id}: an operation id is dotted identifiers")
            if operation_id in seen:
                raise GenerationError(f"{operation_id}: duplicate operation id")
            seen.add(operation_id)
            where = operation_id
            binary_direction = config.binary.get(operation_id)
            if binary_direction not in (None, "request", "response"):
                raise GenerationError(f"{where}: unknown binary direction {binary_direction!r}")
            relative_path = _relative_path(path, config.default_prefix, where=where)
            body_model = None
            body_dump = ""
            if "requestBody" in operation and binary_direction != "request":
                body_schema = json_schema_of(operation["requestBody"])
                if body_schema is None:
                    raise GenerationError(f"{where}: the request body is not application/json")
                if "$ref" not in body_schema:
                    raise GenerationError(f"{where}: the request body is not a component model")
                name = ref_name(body_schema["$ref"])
                body_model = shared.get(name, name)
                body_dump = body_dump_expression(name, schemas, where=where)
            response_kind, response_model = response_shape(
                operation, where, shared, binary_direction
            )
            parameters = operation.get("parameters", [])
            operations.append(
                Operation(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=relative_path,
                    description=operation.get("description", ""),
                    path_params=tuple(
                        build_parameter(item, shared, where=where)
                        for item in parameters
                        if item["in"] == "path"
                    ),
                    query_params=tuple(
                        build_parameter(item, shared, where=where)
                        for item in parameters
                        if item["in"] == "query"
                    ),
                    body_model=body_model,
                    body_dump=body_dump,
                    response_kind=response_kind,
                    response_model=response_model,
                    binary_direction=binary_direction,
                )
            )
    return operations


def schema_references(schema: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(schema, dict):
        reference = schema.get("$ref")
        if isinstance(reference, str):
            found.add(ref_name(reference))
        for value in schema.values():
            found.update(schema_references(value))
    elif isinstance(schema, list):
        for value in schema:
            found.update(schema_references(value))
    return found


def collect_models(
    roots: Iterable[str], schemas: Mapping[str, Any], shared: Mapping[str, str]
) -> dict[str, Mapping[str, Any]]:
    """Collect every component schema one group references, dependencies first."""
    collected: dict[str, Mapping[str, Any]] = {}
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in collected or name in shared:
            continue
        schema = schemas.get(name)
        if schema is None:
            raise GenerationError(f"the schema {name!r} is not defined in the OpenAPI document")
        collected[name] = schema
        pending.extend(sorted(schema_references(schema) - set(collected)))
    return collected


def order_models(models: Mapping[str, Mapping[str, Any]], shared: Mapping[str, str]) -> list[str]:
    """Dependencies first: pydantic resolves field annotations at class creation."""
    ordered: list[str] = []
    emitted: set[str] = set()
    remaining = sorted(models)
    while remaining:
        ready = [
            name
            for name in remaining
            if schema_references(models[name]) - emitted <= set(shared) | emitted
        ]
        if not ready:
            raise GenerationError(f"the models {remaining} form a reference cycle")
        for name in ready:
            ordered.append(name)
            emitted.add(name)
        remaining = [name for name in remaining if name not in emitted]
    return ordered


def class_name(chain: Sequence[str]) -> str:
    return "".join(part.replace("_", " ").title().replace(" ", "") for part in chain) + "Client"


def sanitize(text: str) -> str:
    return text.replace('"""', "'''").strip()


def insert_operation(node: SubClient, chain: tuple[str, ...], operation: Operation) -> None:
    if not chain:
        node.operations = (*node.operations, operation)
        return
    segment, rest = chain[0], chain[1:]
    child = next((item for item in node.children if item.name == segment), None)
    if child is None:
        child = SubClient(name=segment, prefix="", class_name="", children=(), operations=())
        node.children = (*node.children, child)
    insert_operation(child, rest, operation)


def finalize_tree(node: SubClient, prefix: str) -> SubClient:
    return SubClient(
        name=node.name,
        prefix=prefix,
        class_name=class_name(prefix.split(".")) if node.name else "",
        children=tuple(
            finalize_tree(child, f"{prefix}.{child.name}")
            for child in sorted(node.children, key=lambda item: item.name)
        ),
        operations=node.operations,
    )


def build_tree(operations: Sequence[Operation], group: str) -> SubClient:
    """Nest operations into the sub-client tree their operation ids describe.

    The root carries the group itself: an operation whose id has no sub-client
    segment becomes a method of the group client.
    """
    root = SubClient(name="", prefix=group, class_name="", children=(), operations=())
    for operation in operations:
        insert_operation(root, operation.chain, operation)
    return finalize_tree(root, group)


def sub_client_classes(node: SubClient) -> list[SubClient]:
    """Children before parents: a parent constructs its children."""
    classes: list[SubClient] = []
    for child in node.children:
        classes.extend(sub_client_classes(child))
    if node.name:
        classes.append(node)
    return classes


def render_models(
    names: Sequence[str], models: Mapping[str, Mapping[str, Any]], shared: Mapping[str, str]
) -> list[str]:
    writer = Writer()
    for index, name in enumerate(names):
        if index:
            writer.blank()
            writer.blank()
        schema = models[name]
        writer.line(f"class {name}(BaseModel):")
        description = schema.get("description")
        if description:
            writer.line(f'    """{sanitize(description)}"""')
            writer.blank()
        properties = schema.get("properties", {})
        if not properties:
            writer.line("    pass")
            continue
        required = set(schema.get("required", []))
        for field, value in properties.items():
            annotation = python_type(value, shared, where=f"{name}.{field}")
            if field in required and "default" not in value:
                writer.line(f"    {field}: {annotation}")
                continue
            default = value.get("default")
            if default is None and not annotation.endswith("None"):
                annotation = f"{annotation} | None"
            writer.line(f"    {field}: {annotation} = {render_default(default)}")
    return writer.lines


def render_request_call(operation: Operation, indent: int) -> list[str]:
    # The resource path is relative; the channel supplies the runtime API root
    # so one generated client serves any configured OPSMESH_API_PREFIX. Path
    # parameters stay inside the same f-string.
    path_literal = f'f"{{self._channel.api_root}}{operation.path}"'
    arguments = [("method", f'"{operation.method}"'), ("path", path_literal)]
    if operation.query_params:
        arguments.append(("query", "query"))
    if operation.body_model is not None:
        arguments.append(("body", operation.body_dump))
    if operation.binary_direction == "request":
        arguments.append(("headers", '{"content-type": content_type}'))
        arguments.append(("content", "content"))
    single = f"TransportRequest({', '.join(f'{name}={value}' for name, value in arguments)}),"
    pad = " " * indent
    if indent + len(single) <= MAX_LINE:
        return [pad + single]
    lines = [pad + "TransportRequest("]
    lines.extend(f"{pad}    {name}={value}," for name, value in arguments)
    lines.append(pad + "),")
    return lines


def render_signature(operation: Operation) -> list[str]:
    positional = [f"{p.name}: {p.annotation}" for p in operation.path_params]
    if operation.body_model is not None:
        positional.append(f"request: {operation.body_model}")
    if operation.binary_direction == "request":
        positional.append("content: bytes")
    keyword = [
        f"{p.name}: {p.annotation}"
        if p.default is None
        else f"{p.name}: {p.annotation} = {p.default}"
        for p in operation.query_params
    ]
    if operation.binary_direction == "request":
        keyword.append("content_type: str")
    returns = {
        "model": operation.response_model or "None",
        "array": f"list[{operation.response_model}]",
        "binary": "bytes",
        "none": "None",
    }[operation.response_kind]
    parts = ["self", *positional]
    if keyword:
        parts.append("*")
        parts.extend(keyword)
    single = f"    async def {operation.method_name}({', '.join(parts)}) -> {returns}:"
    if len(single) <= MAX_LINE:
        return [single]
    lines = [f"    async def {operation.method_name}("]
    lines.append("        self,")
    lines.extend(f"        {parameter}," for parameter in positional)
    if keyword:
        lines.append("        *,")
        lines.extend(f"        {parameter}," for parameter in keyword)
    lines.append(f"    ) -> {returns}:")
    return lines


def render_method(operation: Operation) -> list[str]:
    writer = Writer()
    writer.extend(render_signature(operation))
    if operation.description:
        writer.line(f'        """{sanitize(operation.description)}"""')
    if operation.query_params:
        writer.line("        query = {")
        writer.line("            name: str(value)")
        writer.line("            for name, value in (")
        for parameter in operation.query_params:
            writer.line(f'                ("{parameter.name}", {parameter.name}),')
        writer.line("            )")
        writer.line("            if value is not None")
        writer.line("        }")
    assignment = "" if operation.response_kind == "none" else "response = "
    writer.line(f"        {assignment}await self._channel.transport.send(")
    writer.extend(render_request_call(operation, REQUEST_INDENT))
    writer.line("            self._channel.context(),")
    writer.line("        )")
    if operation.response_kind == "model":
        single = f"        return {operation.response_model}.model_validate(response.body)"
        if len(single) <= MAX_LINE:
            writer.line(single)
        else:
            writer.line(f"        return {operation.response_model}.model_validate(")
            writer.line("            response.body")
            writer.line("        )")
    elif operation.response_kind == "array":
        single = (
            f"        return [{operation.response_model}.model_validate(item) "
            "for item in decode_array(response)]"
        )
        if len(single) <= MAX_LINE:
            writer.line(single)
        else:
            writer.line("        return [")
            writer.line(f"            {operation.response_model}.model_validate(item)")
            writer.line("            for item in decode_array(response)")
            writer.line("        ]")
    elif operation.response_kind == "binary":
        writer.line("        return decode_binary(response)")
    return writer.lines


def render_sub_client(node: SubClient) -> list[str]:
    writer = Writer()
    writer.line(f"class {node.class_name}:")
    writer.line(f'    """Generated client for the ``{node.prefix}`` operations."""')
    writer.blank()
    writer.line("    def __init__(self, channel: GroupChannel) -> None:")
    writer.line("        self._channel = channel")
    for child in node.children:
        single = f"        self.{child.name} = {child.class_name}(channel)"
        if len(single) <= MAX_LINE:
            writer.line(single)
        else:
            writer.line(f"        self.{child.name} = {child.class_name}(")
            writer.line("            channel")
            writer.line("        )")
    for operation in node.operations:
        writer.blank()
        writer.extend(render_method(operation))
    return writer.lines


def operation_annotations(operation: Operation) -> list[str]:
    annotations = [p.annotation for p in (*operation.path_params, *operation.query_params)]
    if operation.body_model is not None:
        annotations.append(operation.body_model)
    if operation.response_model is not None:
        annotations.append(operation.response_model)
    return annotations


def render_group_module(
    group: str, operations: Sequence[Operation], document: Mapping[str, Any], config: CodegenConfig
) -> str:
    schemas = document["components"]["schemas"]
    shared = config.shared_values
    roots: set[str] = set()
    for operation in operations:
        if operation.body_model is not None and operation.body_model not in shared:
            roots.add(operation.body_model)
        if operation.response_model is not None and operation.response_model not in shared:
            roots.add(operation.response_model)
    models = collect_models(roots, schemas, shared)
    ordered = order_models(models, shared)
    tree = build_tree(operations, group)
    classes = sub_client_classes(tree)

    model_annotations = [
        python_type(value, shared, where=f"{name}.{field}")
        for name, schema in models.items()
        for field, value in schema.get("properties", {}).items()
    ]
    annotations = [
        annotation
        for operation in operations
        for annotation in operation_annotations(operation)
    ]
    uses_uuid = any("UUID" in annotation for annotation in (*annotations, *model_annotations))
    uses_datetime = any(
        "datetime" in annotation for annotation in (*annotations, *model_annotations)
    )
    uses_array = any(operation.response_kind == "array" for operation in operations)
    uses_binary = any(
        operation.response_kind == "binary" or operation.binary_direction == "request"
        for operation in operations
    )
    shared_used = sorted(
        {
            name
            for name in (*annotations, *model_annotations)
            if name is not None and name in shared
        }
    )

    writer = Writer()
    writer.line(f'"""Generated {group} API group; regenerate instead of editing by hand.')
    writer.blank()
    writer.line("Produced by scripts/generate_sdk_api.py from docs/openapi.json and")
    writer.line("sdk/python/codegen.toml; the platform OpenAPI is the HTTP contract.")
    writer.line("Stable operation ids of this group:")
    writer.blank()
    for operation in operations:
        writer.line(f"- ``{operation.operation_id}``")
    writer.line('"""')
    writer.blank()
    writer.line("from __future__ import annotations")
    writer.blank()
    if uses_datetime:
        writer.line("from datetime import datetime")
    if uses_uuid:
        writer.line("from uuid import UUID")
    if uses_datetime or uses_uuid:
        writer.blank()
    writer.line("from pydantic import BaseModel")
    writer.blank()
    contract_imports = {"GroupChannel", "TransportRequest"}
    if uses_array:
        contract_imports.add("decode_array")
    if uses_binary:
        contract_imports.add("decode_binary")
    writer.line("from opsmesh_sdk.transport.contracts import (")
    writer.extend(f"    {name}," for name in sorted(contract_imports))
    writer.line(")")
    if shared_used:
        writer.blank()
        writer.line(f"from {shared[shared_used[0]]} import (")
        writer.extend(f"    {name}," for name in shared_used)
        writer.line(")")
    if ordered:
        writer.blank()
        writer.blank()
        writer.extend(render_models(ordered, models, shared))
    for node in classes:
        writer.blank()
        writer.blank()
        writer.extend(render_sub_client(node))
    writer.blank()
    writer.blank()
    group_class = class_name((group,))
    writer.line(f"class {group_class}:")
    writer.line(f'    """The {group} API group."""')
    writer.blank()
    writer.line("    def __init__(self, channel: GroupChannel) -> None:")
    if tree.operations:
        writer.line("        self._channel = channel")
    for child in tree.children:
        writer.line(f"        self.{child.name} = {child.class_name}(channel)")
    for operation in tree.operations:
        writer.blank()
        writer.extend(render_method(operation))
    writer.blank()
    writer.blank()
    writer.line(f"def create_{group}_client(channel: GroupChannel) -> {group_class}:")
    writer.line(f'    """Create the {group} group client over one composed platform channel."""')
    writer.line(f"    return {group_class}(channel)")
    writer.blank()
    writer.blank()
    exported = sorted(
        [*ordered, *(node.class_name for node in classes), group_class, f"create_{group}_client"]
    )
    writer.line("__all__ = [")
    writer.extend(f'    "{name}",' for name in exported)
    writer.line("]")
    return writer.render()


def generate(config: CodegenConfig, document: Mapping[str, Any]) -> dict[Path, str]:
    operations = collect_operations(document, config)
    by_group: dict[str, list[Operation]] = {}
    for operation in operations:
        by_group.setdefault(operation.group, []).append(operation)
    known = {client.group for client in config.clients}
    unknown = sorted(set(by_group) - known)
    if unknown:
        raise GenerationError(f"operations declare undeclared client groups: {unknown}")
    documents: dict[Path, str] = {}
    for group, group_operations in sorted(by_group.items()):
        group_operations.sort(key=lambda operation: (operation.path, operation.method))
        documents[config.target_of(group)] = render_group_module(
            group, group_operations, document, config
        )
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail on missing or stale artifacts")
    args = parser.parse_args()
    config = load_config()
    document = load_document(config)
    try:
        documents = generate(config, document)
    except GenerationError as exc:
        print(f"SDK generation failed: {exc}")
        return 1
    if args.check:
        stale = [
            path
            for path, content in sorted(documents.items())
            if not path.exists() or path.read_text(encoding="utf-8") != content
        ]
        if stale:
            print("Generated SDK clients are out of date:")
            for path in stale:
                print(path.relative_to(ROOT).as_posix())
            return 1
        print(f"SDK generation verified: {len(documents)} generated files")
        return 0
    for path, content in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Generated {len(documents)} SDK client files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
