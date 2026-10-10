"""Export the registered API contract and its Markdown reference without starting services."""

from __future__ import annotations

import argparse
import inspect
import json
import os
from collections import defaultdict
from enum import Enum
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def cell(value: object) -> str:
    return str(value).replace("|", "&#124;").replace("\n", "<br>")


def references(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set()
        if "$ref" in value:
            found.add(value["$ref"].removeprefix("#/components/schemas/"))
        for item in value.values():
            found.update(references(item))
        return found
    if isinstance(value, list):
        return set().union(*(references(item) for item in value))
    return set()


def model_link(name: str) -> str:
    return f"[{name}](schemas.md#schema-{name})"


def schema_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return model_link(schema["$ref"].rsplit("/", 1)[1])
    for union in ("anyOf", "oneOf", "allOf"):
        if union in schema:
            return f" {union} ".join(schema_type(item) for item in schema[union])
    if schema.get("type") == "array":
        return f"array&lt;{schema_type(schema.get('items', {}))}&gt;"
    kind = schema.get("type", "any")
    if "format" in schema:
        kind += f" ({schema['format']})"
    return kind


def constraints(schema: dict[str, Any]) -> str:
    ignored = {"type", "title", "description", "properties", "$ref", "items"}
    parts = [schema["description"]] if schema.get("description") else []
    parts.extend(
        f"`{key}={json.dumps(value, ensure_ascii=False, sort_keys=True)}`"
        for key, value in sorted(schema.items())
        if key not in ignored
    )
    return cell("; ".join(parts)) or "—"


def dependency_details(route: Any) -> list[str]:
    details: list[str] = []
    visited: set[int] = set()

    def walk(dependency: Any) -> None:
        if id(dependency) in visited:
            return
        visited.add(id(dependency))
        call = dependency.call
        if inspect.isfunction(call):
            name = f"{call.__module__}.{call.__qualname__}"
            scope = inspect.getclosurevars(call).nonlocals
            actions = ", ".join(
                f"{key}={value.value}"
                for key, value in sorted(scope.items())
                if isinstance(value, Enum)
            )
            if call.__name__ in {
                "get_current_user",
                "require_platform_admin",
                "require_workspace_context",
                "require_account_action",
                "get_authenticated_worker",
                "plugin_principal",
            }:
                details.append(f"`{name}`" + (f"：`{actions}`" if actions else ""))
        for child in dependency.dependencies:
            walk(child)

    for dependency in route.dependant.dependencies:
        walk(dependency)
    return list(dict.fromkeys(details))


def render_operation(path: str, method: str, operation: dict[str, Any], route: Any) -> str:
    source = Path(inspect.getfile(route.endpoint)).resolve().relative_to(ROOT).as_posix()
    lines = [
        f"## {method.upper()} `{path}`",
        "",
        operation.get("summary", route.endpoint.__name__),
        "",
        f"Operation ID：`{operation['operationId']}`。",
        "",
        f"实现：[{source}](../../{source}) · `{route.endpoint.__name__}`。",
        "",
    ]
    if operation.get("description"):
        lines.extend([operation["description"], ""])
    guards = dependency_details(route)
    lines.extend(
        [
            "权限依赖："
            + (
                "；".join(guards)
                if guards
                else "未声明上述认证依赖；签名、注册令牌或其他服务层校验以实现为准。"
            ),
            "",
        ]
    )
    parameters = operation.get("parameters", [])
    if parameters:
        lines.extend(
            [
                "### 参数",
                "",
                "| 名称 | 位置 | 必填 | 类型 | 说明与约束 |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for parameter in parameters:
            schema = parameter.get("schema", {})
            lines.append(
                f"| `{parameter['name']}` | {parameter['in']} | "
                f"{'是' if parameter.get('required') else '否'} | "
                f"{schema_type(schema)} | {constraints(schema)} |"
            )
        lines.append("")
    else:
        lines.extend(["参数：无显式路径、查询或 Header 参数。", ""])
    body = operation.get("requestBody")
    if body:
        lines.extend(["### 请求体", "", f"必填：{'是' if body.get('required') else '否'}。", ""])
        for media, content in sorted(body.get("content", {}).items()):
            schema = content.get("schema", {})
            lines.extend([f"Content-Type：`{media}`。", "", "```json", dump(schema), "```", ""])
            refs = sorted(references(schema))
            if refs:
                lines.extend(["模型：" + "、".join(model_link(name) for name in refs), ""])
    lines.extend(
        ["### 响应", "", "| HTTP | 说明 | Content-Type | 返回类型 |", "| --- | --- | --- | --- |"]
    )
    for code, response in sorted(operation.get("responses", {}).items()):
        content = response.get("content", {})
        for media, value in content.items() or [("—", {})]:
            lines.append(
                f"| {code} | {cell(response.get('description', ''))} | "
                f"{media} | {schema_type(value.get('schema', {})) if content else '无响应体'} |"
            )
    lines.extend(["", "其他错误及请求追踪约定见 [API 使用说明](../api.md#错误与追踪)。", ""])
    return "\n".join(lines)


def render_schemas(schemas: dict[str, Any]) -> str:
    lines = [
        "# API 数据模型",
        "",
        "此文件由当前 OpenAPI 生成。字段表和完整 JSON Schema 同时保留；"
        "`required` 是字段是否必须出现，`null` 是字段值能否为空，两者含义不同。",
        "",
        "[返回接口索引](README.md)",
        "",
    ]
    for name, schema in sorted(schemas.items()):
        lines.extend([f'<a id="schema-{name}"></a>', "", f"## {name}", ""])
        if schema.get("description"):
            lines.extend([schema["description"], ""])
        properties = schema.get("properties", {})
        if properties:
            lines.extend(["| 字段 | 类型 | 必填 | 说明与约束 |", "| --- | --- | --- | --- |"])
            for field, value in properties.items():
                lines.append(
                    f"| `{field}` | {schema_type(value)} | "
                    f"{'是' if field in schema.get('required', []) else '否'} | "
                    f"{constraints(value)} |"
                )
            lines.append("")
        lines.extend(["```json", dump(schema), "```", ""])
    return "\n".join(lines)


def build_documents() -> dict[Path, str]:
    # This subprocess exports a canonical /api/v1 contract, without telemetry exporters.
    os.environ.update(
        {
            "OPSMESH_ENVIRONMENT": "test",
            "OPSMESH_API_PREFIX": "/api/v1",
            "OPSMESH_ENABLE_API_DOCS": "true",
            "OPSMESH_TRACING_ENABLED": "false",
            "OPSMESH_OTEL_LOGS_ENABLED": "false",
        }
    )
    from fastapi.routing import APIRoute

    from opsmesh.main import app

    document = app.openapi()
    routes = {
        (route.path, method.lower()): route
        for route in app.routes
        if isinstance(route, APIRoute) and route.include_in_schema
        for method in route.methods
    }
    groups: dict[str, list[tuple[str, str, dict[str, Any], Any]]] = defaultdict(list)
    operations = set()
    for path, path_item in sorted(document["paths"].items()):
        for method, operation in sorted(path_item.items()):
            if method not in METHODS:
                continue
            key = (path, method)
            operations.add(key)
            tag = operation.get("tags", ["untagged"])[0]
            groups[tag].append((path, method, operation, routes[key]))
    if operations != set(routes):
        raise ValueError("OpenAPI operations do not exactly cover registered API routes")
    schemas = document["components"]["schemas"]
    missing = references(document) - schemas.keys()
    if missing:
        raise ValueError(f"Unresolved schema references: {sorted(missing)}")
    documents = {
        ROOT / "docs/openapi.json": dump(document) + "\n",
        ROOT / "docs/api/schemas.md": render_schemas(schemas),
    }
    index = [
        "# 完整接口参考",
        "",
        "由 `opsmesh.main` 实际注册的路由和 OpenAPI 自动生成。",
        "",
        f"共 **{len(operations)} 个接口**、**{len(document['paths'])} 个路径**、"
        f"**{len(schemas)} 个模型**。默认前缀 `/api/v1`。",
        "",
        "[使用说明](../api.md) · [OpenAPI JSON](../openapi.json) · [全部数据模型](schemas.md)",
        "",
        "| 接口分组 | 接口数 |",
        "| --- | --- |",
    ]
    for tag, entries in sorted(groups.items()):
        filename = f"{tag}.md"
        index.append(f"| [{tag}]({filename}) | {len(entries)} |")
        lines = [
            f"# {tag}",
            "",
            "[返回接口索引](README.md)",
            "",
            "| 方法 | 路径 | 操作 |",
            "| --- | --- | --- |",
        ]
        for path, method, operation, _ in entries:
            lines.append(f"| {method.upper()} | `{path}` | {cell(operation.get('summary', ''))} |")
        lines.append("")
        lines.extend(render_operation(*entry) for entry in entries)
        documents[ROOT / "docs/api" / filename] = "\n".join(lines)
    documents[ROOT / "docs/api/README.md"] = "\n".join(index) + "\n"
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail on missing or stale artifacts")
    args = parser.parse_args()
    documents = build_documents()
    existing = set((ROOT / "docs/api").glob("*.md"))
    stale = existing - documents.keys()
    changed = [
        path
        for path, content in documents.items()
        if not path.exists() or path.read_text(encoding="utf-8") != content
    ]
    if args.check:
        if changed or stale:
            print("API documentation is out of date:")
            for path in sorted(set(changed) | stale):
                print(path.relative_to(ROOT).as_posix())
            return 1
        print(f"API documentation verified: {len(documents)} generated files")
        return 0
    for path in stale:
        path.unlink()
    for path, content in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Exported {len(documents)} API documentation files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
