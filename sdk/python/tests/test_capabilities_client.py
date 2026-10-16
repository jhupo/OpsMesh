"""Generated capabilities group contract tests: catalog, versions and packages."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx

from opsmesh_sdk.api.capabilities import ExpertCreateRequest, create_capabilities_client
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.transport.http import HttpTransport

DEFINITION_ID = UUID("11111111-2222-3333-4444-555555555555")


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


def version_payload() -> dict[str, Any]:
    return {
        "definition_id": str(DEFINITION_ID),
        "version": 1,
        "status": "draft",
        "configuration": {"instructions": "Implement carefully."},
        "resource_manifest": [],
        "dependencies": [],
        "flow_templates": [],
        "digest": None,
        "package_digest": None,
        "package_size": None,
        "package_filename": None,
        "published_at": None,
        "published_by_user_id": None,
        "created_at": "2026-10-17T00:00:00Z",
    }


def test_expert_create_posts_the_type_configuration() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json=version_payload())

    group = capabilities_client(handler)
    result = asyncio.run(
        group.experts.create(
            ExpertCreateRequest(
                key="coding",
                name="Coding Expert",
                configuration={
                    "instructions": "Implement carefully.",
                    "capability_slots": [],
                    "parameters_schema": {"type": "object", "properties": {}},
                    "secret_slots": [],
                },
            )
        )
    )
    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v1/capabilities/experts"
    assert captured["body"]["key"] == "coding"
    assert captured["body"]["configuration"]["instructions"] == "Implement carefully."
    assert result.version == 1
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
                        "type": "expert",
                        "key": "coding",
                        "name": "Coding Expert",
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
    page = asyncio.run(group.definitions.list(type="expert", source="system", limit=10, offset=0))
    assert captured["path"] == "/api/v1/capabilities/definitions"
    assert captured["query"]["type"] == "expert"
    assert captured["query"]["source"] == "system"
    assert page.total == 1
    assert page.items[0].key == "coding"


def test_version_publish_and_package_read() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        if request.url.path.endswith("/package"):
            return httpx.Response(
                200, content=b"zip-bytes", headers={"content-type": "application/zip"}
            )
        return httpx.Response(200, json=version_payload())

    group = capabilities_client(handler)
    published = asyncio.run(group.versions.publish(DEFINITION_ID, 1))
    assert captured["method"] == "POST"
    assert published.status == "draft"
    payload = asyncio.run(group.versions.package.read(DEFINITION_ID, 1))
    assert payload == b"zip-bytes"


def test_package_attach_sends_raw_bytes() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["content_type"] = request.headers.get("content-type")
        captured["content"] = request.content
        return httpx.Response(200, json=version_payload())

    group = capabilities_client(handler)
    asyncio.run(
        group.experts.package.attach(
            DEFINITION_ID, b"package-bytes", content_type="application/zip"
        )
    )
    assert captured["method"] == "PUT"
    assert captured["content_type"] == "application/zip"
    assert captured["content"] == b"package-bytes"
