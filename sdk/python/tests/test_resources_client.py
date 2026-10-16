"""Generated resources group contract tests: files, versions, grants and artifacts."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID

import httpx

from opsmesh_sdk.api.resources import (
    DirectoryCreateRequest,
    FileCreateRequest,
    GrantCreateRequest,
    RuntimePolicyRequest,
    create_resources_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.transport.http import HttpTransport

FILE_ID = UUID("22222222-3333-4444-5555-666666666666")
DIRECTORY_ID = UUID("33333333-4444-5555-6666-777777777777")
GRANT_ID = UUID("44444444-5555-6666-7777-888888888888")


def resources_client(handler: Any) -> Any:
    client = PlatformClient(
        "https://api.invalid",
        session_id="session-1",
        transport=HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        ),
    )
    return create_resources_client(client)


def version_payload() -> dict[str, Any]:
    return {
        "file_id": str(FILE_ID),
        "version": 1,
        "digest": "a" * 64,
        "size": 5,
        "media_type": "text/plain",
        "created_by_user_id": str(FILE_ID),
        "created_at": "2026-10-17T00:00:00Z",
    }


def test_directory_create_posts_the_path() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "directory_id": str(DIRECTORY_ID),
                "path": "docs",
                "parent_id": None,
                "created_at": "2026-10-17T00:00:00Z",
            },
        )

    group = resources_client(handler)
    directory = asyncio.run(group.files.directories.create(DirectoryCreateRequest(path="docs")))
    assert captured["path"] == "/api/v1/resources/files/directories"
    assert captured["body"]["path"] == "docs"
    assert directory.directory_id == DIRECTORY_ID


def test_content_write_sends_bytes_and_its_query_contract() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["query"] = dict(request.url.params)
        captured["content"] = request.content
        captured["content_type"] = request.headers.get("content-type")
        return httpx.Response(200, json=version_payload())

    group = resources_client(handler)
    version = asyncio.run(
        group.files.content.write(
            FILE_ID,
            b"hello",
            expected_version=1,
            media_type="text/plain",
            idempotency_key="key-1",
            content_type="text/plain",
        )
    )
    assert captured["method"] == "PUT"
    assert captured["path"] == f"/api/v1/resources/files/{FILE_ID}/content"
    assert captured["query"]["expected_version"] == "1"
    assert captured["query"]["idempotency_key"] == "key-1"
    assert captured["content"] == b"hello"
    assert version.version == 1


def test_content_read_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/resources/files/{FILE_ID}/content"
        return httpx.Response(200, content=b"file-bytes", headers={"content-type": "text/plain"})

    group = resources_client(handler)
    payload = asyncio.run(group.files.content.read(FILE_ID))
    assert payload == b"file-bytes"


def test_grant_create_and_runtime_policy_update() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = json.loads(request.content)
        if request.url.path.endswith("/grants"):
            return httpx.Response(
                201,
                json={
                    "grant_id": str(GRANT_ID),
                    "grantee_user_id": str(FILE_ID),
                    "permissions": ["read"],
                    "created_at": "2026-10-17T00:00:00Z",
                },
            )
        return httpx.Response(
            200,
            json={
                "file_id": str(FILE_ID),
                "frozen_input_versions": [1],
                "relative_paths": ["docs/notes.txt"],
                "output_declarations": [],
                "use_in_execution": True,
                "updated_at": "2026-10-17T00:00:00Z",
            },
        )

    group = resources_client(handler)
    grant = asyncio.run(
        group.files.grants.create(
            FILE_ID, GrantCreateRequest(grantee_user_id=FILE_ID, permissions=["read"])
        )
    )
    assert grant.permissions == ["read"]
    policy = asyncio.run(
        group.files.runtime_policy.update(
            FILE_ID,
            RuntimePolicyRequest(
                frozen_input_versions=[1],
                relative_paths=["docs/notes.txt"],
                use_in_execution=True,
            ),
        )
    )
    assert policy.use_in_execution is True
    assert policy.frozen_input_versions == [1]


def test_file_create_and_version_diff() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        if request.url.path.endswith("/diff"):
            return httpx.Response(
                200,
                json={
                    "from_version": 1,
                    "to_version": 2,
                    "identical": False,
                    "from_size": 5,
                    "to_size": 9,
                    "size_delta": 4,
                },
            )
        return httpx.Response(
            201,
            json={
                "file_id": str(FILE_ID),
                "directory_id": str(DIRECTORY_ID),
                "name": "notes.txt",
                "owner_user_id": str(FILE_ID),
                "latest_version": 0,
                "created_at": "2026-10-17T00:00:00Z",
                "updated_at": "2026-10-17T00:00:00Z",
            },
        )

    group = resources_client(handler)
    resource = asyncio.run(
        group.files.create(FileCreateRequest(directory_id=DIRECTORY_ID, name="notes.txt"))
    )
    assert resource.name == "notes.txt"
    diff = asyncio.run(group.files.versions.diff(FILE_ID, from_version=1, to_version=2))
    assert diff.identical is False
    assert diff.size_delta == 4
