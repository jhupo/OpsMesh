"""Platform command clients preserve operation identities and admission facts."""

import asyncio
import json
from uuid import uuid4

import httpx

from opsmesh_sdk.api.platform import PlatformOperationCreate, create_platform_client
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.transport.http import HttpTransport


def test_operation_submission_and_cancellation_keep_the_same_receipt():
    operation_id, administrator_id = uuid4(), uuid4()
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/cancel"):
            assert request.method == "POST"
            assert request.url.path.endswith(f"/{operation_id}/cancel")
            status = "cancelled"
        else:
            assert json.loads(request.content) == {
                "owner": "platform",
                "command": "admission.drain",
                "idempotency_key": "drain-once",
            }
            status = "pending"
        return httpx.Response(
            200 if status == "cancelled" else 202,
            json={
                "id": str(operation_id),
                "owner": "platform",
                "command": "admission.drain",
                "target": {},
                "idempotency_key": "drain-once",
                "requested_by_user_id": str(administrator_id),
                "status": status,
                "result": {},
                "failure_code": None,
                "error_detail": None,
            },
        )

    async def verify():
        transport = HttpTransport(
            "https://api.invalid",
            session_id="session-1",
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        client = create_platform_client(PlatformClient("https://api.invalid", transport=transport))
        submitted = await client.operations.submit(
            PlatformOperationCreate(
                owner="platform", command="admission.drain", idempotency_key="drain-once"
            )
        )
        cancelled = await client.operations.cancel(submitted.id)
        assert cancelled.id == submitted.id == operation_id
        assert cancelled.status == "cancelled"
        await transport.aclose()

    asyncio.run(verify())
    assert len(calls) == 2
