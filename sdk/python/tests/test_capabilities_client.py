"""Generated capabilities group contract tests: catalog, versions and packages."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx

from opsmesh_sdk.api.capabilities import DefinitionInput, create_capabilities_client
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.transport.http import HttpTransport

DEFINITION_ID = UUID("11111111-2222-3333-4444-555555555555")
VERSION_ID = UUID("66666666-7777-8888-9999-aaaaaaaaaaaa")


def capabilities_client(handler: Any) -> Any:
    client = PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    return create_capabilities_client(client)


def test_tool_create_posts_the_type_configuration(capability_version_payload) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=capability_version_payload)

    group = capabilities_client(handler)
    result = asyncio.run(
        group.definitions.create(
            DefinitionInput.model_validate(
                dict(
                    key="coding",
                    name="Coding Tool",
                    declaration={
                        "type": "tool",
                        "configuration": {
                            "entrypoint": "tool.main:run",
                            "execution_mode": "sandbox",
                            "operations": [],
                            "parameters_schema": {"type": "object", "properties": {}},
                        },
                    },
                )
            )
        )
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/capabilities/definitions"
    assert captured["body"]["key"] == "coding"
    assert captured["body"]["declaration"]["configuration"]["entrypoint"] == "tool.main:run"
    assert result.version == 1
    assert result.version_id == VERSION_ID
    assert result.status == "draft"


def test_definitions_list_sends_its_filters() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "definition_id": str(DEFINITION_ID),
                        "type": "tool",
                        "key": "coding",
                        "name": "Coding Tool",
                        "description": "",
                        "visibility": "public",
                        "status": "active",
                        "source": "system",
                        "created_by_user_id": None,
                        "latest_version": 1,
                        "has_published_version": True,
                        "created_at": "2026-10-17T00:00:00Z",
                        "updated_at": "2026-10-17T00:00:00Z",
                    }
                ],
                "total": 1,
            },
        )

    group = capabilities_client(handler)
    page = asyncio.run(group.definitions.list(type="tool", source="system", limit=10, offset=0))
    assert captured["path"] == "/api/v1/capabilities/definitions"
    assert captured["query"]["type"] == "tool"
    assert captured["query"]["source"] == "system"
    assert page.total == 1
    assert page.items[0].key == "coding"


def test_version_publish_and_package_read(capability_version_payload) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        if request.url.path.endswith("/package"):
            return httpx.Response(
                200, content=b"zip-bytes", headers={"content-type": "application/zip"}
            )
        return httpx.Response(200, json={**capability_version_payload, "status": "published"})

    group = capabilities_client(handler)
    published = asyncio.run(group.versions.publish(DEFINITION_ID, 1))
    assert captured["method"] == "POST"
    assert published.status == "published"
    assert published.version_id == VERSION_ID
    payload = asyncio.run(group.versions.package.read(DEFINITION_ID, 1))
    assert payload == b"zip-bytes"


def test_package_attach_sends_raw_bytes(capability_version_payload) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["content_type"] = request.headers.get("content-type")
        captured["content"] = request.content
        return httpx.Response(200, json=capability_version_payload)

    group = capabilities_client(handler)
    asyncio.run(
        group.definitions.package.attach(
            DEFINITION_ID, b"package-bytes", content_type="application/zip"
        )
    )
    assert captured["method"] == "PUT"
    assert captured["content_type"] == "application/zip"
    assert captured["content"] == b"package-bytes"


def test_channel_create_and_package_attach_use_the_raw_byte_channel(
    capability_version_payload,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["content_type"] = request.headers.get("content-type")
        captured["content"] = request.content
        return httpx.Response(201, json=capability_version_payload)

    group = capabilities_client(handler)
    created = asyncio.run(
        group.definitions.create(
            DefinitionInput.model_validate(
                dict(
                    key="wecom",
                    name="WeCom channel",
                    declaration={
                        "type": "channel",
                        "configuration": {
                            "protocol": "wecom",
                            "operations": [
                                {"name": "receive", "entrypoint": "wecom.adapter:receive"},
                                {"name": "send", "entrypoint": "wecom.adapter:send"},
                            ],
                        },
                    },
                )
            )
        )
    )
    assert captured["path"] == "/api/v1/capabilities/definitions"
    assert created.version == 1
    asyncio.run(
        group.definitions.package.attach(
            DEFINITION_ID, b"channel-bytes", content_type="application/zip"
        )
    )
    assert captured["method"] == "PUT"
    assert captured["path"] == f"/api/v1/capabilities/definitions/{DEFINITION_ID}/draft/package"
    assert captured["content"] == b"channel-bytes"


def test_extension_create_and_package_attach_use_the_raw_byte_channel(
    capability_version_payload,
) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["content_type"] = request.headers.get("content-type")
        captured["content"] = request.content
        return httpx.Response(201, json=capability_version_payload)

    group = capabilities_client(handler)
    created = asyncio.run(
        group.definitions.create(
            DefinitionInput.model_validate(
                dict(
                    key="run-digest",
                    name="Run digest",
                    declaration={
                        "type": "extension",
                        "configuration": {
                            "operations": [
                                {
                                    "export_id": "summarize",
                                    "description": "Summarize one run.",
                                    "effects": ["read"],
                                    "permissions": ["platform.audit.append"],
                                }
                            ],
                            "permissions": ["platform.audit.append"],
                        },
                    },
                )
            )
        )
    )
    assert captured["path"] == "/api/v1/capabilities/definitions"
    assert created.version == 1
    asyncio.run(
        group.definitions.package.attach(
            DEFINITION_ID, b"extension-bytes", content_type="application/zip"
        )
    )
    assert captured["method"] == "PUT"
    assert captured["path"] == f"/api/v1/capabilities/definitions/{DEFINITION_ID}/draft/package"
    assert captured["content"] == b"extension-bytes"
