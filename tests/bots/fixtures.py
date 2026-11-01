"""Reusable platform test principals, storage and signed QQ events."""

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from opsmesh.framework.adapters import BotRef, ReplyTarget
from opsmesh.framework.agents import AgentSpecification
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.events import DispatchContext, OpsMeshEvent
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.database import AsyncDatabase, VersionedRecordStore
from opsmesh.modules.accounts.api import UserPrincipal
from opsmesh.modules.bots.api import BotAccount


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[VersionedRecordStore]:
    database = AsyncDatabase(f"sqlite+aiosqlite:///{(tmp_path / 'bots.sqlite').as_posix()}")
    records = VersionedRecordStore(database, "test_bots")
    await database.initialize()
    try:
        yield records
    finally:
        await database.close()


async def authorize(actor_id: str, action: str, resource: str) -> None:
    if actor_id not in {"admin", "user"}:
        raise OpsMeshError("FORBIDDEN", "Unauthorized test principal")


def secret(reference: str) -> str:
    return {"env:BOT_SECRET": "test-secret", "env:BOT_TOKEN": "test-token"}[reference]


def account() -> BotAccount:
    return BotAccount(
        id="support",
        self_id="100",
        adapter="qq",
        settings={"app_id": "12345", "api_base_url": "http://127.0.0.1:5700"},
        credentials={"client_secret": "env:BOT_SECRET"},
    )


def target() -> ReplyTarget:
    return ReplyTarget(bot=BotRef(adapter_id="qq", bot_id="support"), channel_id="c2c:200")


def webhook(*, secret_value="test-secret", **overrides) -> tuple[bytes, dict[str, str]]:
    user_id = str(overrides.get("user_id", 200))
    group = overrides.get("message_type") == "group"
    payload = {
        "op": 0,
        "id": f"callback-{overrides.get('message_id', 1)}",
        "t": "GROUP_AT_MESSAGE_CREATE" if group else "C2C_MESSAGE_CREATE",
        "d": {
            "id": str(overrides.get("message_id", 1)),
            "author": {"member_openid": user_id, "user_openid": user_id},
            "content": "hello",
            "timestamp": "2026-11-01T00:00:00+00:00",
            **({"group_openid": str(overrides.get("group_id", 300))} if group else {}),
        },
    }
    body = json.dumps(payload).encode()
    timestamp = str(int(time.time()))
    seed = (secret_value.encode() * 32)[:32]
    signature = Ed25519PrivateKey.from_private_bytes(seed).sign(timestamp.encode() + body).hex()
    return body, {
        "X-Bot-Appid": "12345",
        "X-Signature-Timestamp": timestamp,
        "X-Signature-Ed25519": signature,
    }


async def ignore(context: DispatchContext[OpsMeshEvent]) -> DispatchResult:
    return DispatchResult(context.event.id)


async def resolve_principal(principal_id: str) -> UserPrincipal:
    if principal_id not in {"admin", "user"}:
        raise OpsMeshError("NOT_FOUND", "Unknown local principal")
    return UserPrincipal(id=principal_id, username=principal_id)


async def resolve_agent(reference: str) -> AgentSpecification:
    if reference != "assistant@2":
        raise OpsMeshError("NOT_FOUND", "Published agent revision does not exist")
    return AgentSpecification(id="assistant", name="Assistant", revision=2)
