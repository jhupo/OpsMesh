"""Deployment-owned quiet-time policy and its restricted push boundary."""

import importlib.util
import os
import stat
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from opsmesh_sdk import PluginContext, PluginManifest
from opsmesh_sdk.identity import AuthorizationRequest, InvocationSource, Principal
from opsmesh_sdk.registration import InvocationContext, tool_invocation
from pydantic import SecretStr


@pytest.fixture
def activity(tmp_path, monkeypatch):
    directory = Path(__file__).resolve().parents[2] / "sdk/python/examples/group-activity"
    spec = importlib.util.spec_from_file_location("group_activity_example", directory / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    channel, user = uuid4(), uuid4()
    context = PluginContext(
        manifest=PluginManifest(
            id="group-activity", version="0.1.1", name="Activity", kind="plugin"
        ),
        source_path=directory,
        data_directory=tmp_path / "data",
        config={
            "channel_id": str(channel),
            "scheduler_user_id": str(user),
            "conversation_id": "verified-group",
            "daily_limit": 3,
        },
    )
    module.setup(context)
    assert not context.data_directory.exists()
    clock = Mock(return_value=datetime(2026, 10, 23, 4, tzinfo=UTC).timestamp())
    monkeypatch.setattr(module.time, "time", clock)
    settings = module.Settings.model_validate(dict(context.config))
    state = module.Activity(settings, context.data_directory)
    invocation = InvocationContext(
        operation_id=uuid4(),
        user_id=user,
        is_admin=True,
        config={},
        credentials={"session": SecretStr(str(uuid4()))},
    )
    return module, context, state, clock, invocation


@pytest.mark.skipif(os.name == "nt", reason="POSIX service group permissions")
def test_activity_database_allows_shared_service_group_under_restrictive_umask(activity):
    _, context, state, _, _ = activity
    previous = os.umask(0o077)
    try:
        assert state.candidate() == {"ready": False}
    finally:
        os.umask(previous)
    assert stat.S_IMODE((context.data_directory / "activity.sqlite").stat().st_mode) == 0o660


def test_activity_persists_claims_deduplicates_observation_and_waits_for_new_input(activity):
    module, context, state, clock, _ = activity
    state = module.Activity(
        state.settings.model_copy(update={"daily_limit": 1}), context.data_directory
    )
    assert state.candidate() == {"ready": False}
    state.observe("first")
    clock.return_value += 1800
    state.observe("first")  # A filter retry must not extend the idle timer.
    assert state.candidate() == {"ready": True, "nonce": "first"}
    assert not state.claim("stale")
    assert state.claim("first")
    restored = module.Activity(state.settings, context.data_directory)
    assert not restored.claim("first")
    clock.return_value += 7200
    assert restored.candidate() == {"ready": False}  # No talking to itself.
    restored.observe("second")
    clock.return_value += 1800
    assert restored.candidate() == {"ready": False}  # Daily limit consumed.
    clock.return_value += 86400
    assert restored.candidate() == {"ready": True, "nonce": "second"}


def test_activity_respects_quiet_hours_and_cooldown(activity):
    module, context, state, clock, _ = activity
    state = module.Activity(
        state.settings.model_copy(update={"daily_limit": 3}), context.data_directory
    )
    state.observe("first")
    clock.return_value += 1800
    assert state.claim("first")
    state.observe("second")
    clock.return_value += 1800
    assert state.candidate() == {"ready": False}
    clock.return_value += 5400
    assert state.candidate()["ready"] is True
    clock.return_value = datetime(2026, 10, 23, 15, tzinfo=UTC).timestamp()  # 23:00 local.
    assert state.candidate() == {"ready": False}
    clock.return_value = datetime(2026, 10, 24, 1, tzinfo=UTC).timestamp()  # 09:00 local.
    assert state.candidate()["ready"] is True


@pytest.mark.asyncio
async def test_activity_tools_reject_group_context_and_other_admins(activity):
    _, context, _, _, invocation = activity
    tool = context.tools["idle_candidate"]
    assert tool.admin_only
    principal = Principal(user_id=invocation.user_id, roles=frozenset({"admin"}))
    assert tool.authorize(
        AuthorizationRequest(principal=principal, action="execute", resource="idle")
    )
    group = InvocationSource(channel_id=uuid4(), reply_scope="group")
    assert not tool.authorize(
        AuthorizationRequest(principal=principal, action="execute", resource="idle", source=group)
    )
    with (
        tool_invocation(
            InvocationContext(
                operation_id=uuid4(),
                user_id=invocation.user_id,
                is_admin=True,
                config={},
                source=group,
            )
        ),
        pytest.raises(PermissionError),
    ):
        await tool.fn()
    with (
        tool_invocation(
            InvocationContext(operation_id=uuid4(), user_id=uuid4(), is_admin=True, config={})
        ),
        pytest.raises(PermissionError),
    ):
        await tool.fn()


@pytest.mark.asyncio
async def test_activity_push_rechecks_new_input_and_does_not_retry_uncertain_attempt(
    activity, monkeypatch
):
    module, context, state, clock, invocation = activity
    original_client = httpx.AsyncClient
    receipt, posts = uuid4(), []

    def respond(request):
        if request.method == "GET":
            state.observe("new-human-message")
            return httpx.Response(
                200,
                json=[
                    {
                        "receipt_id": str(receipt),
                        "conversation": {"external_id": "verified-group", "kind": "group"},
                    }
                ],
            )
        posts.append(request)
        raise httpx.ReadTimeout("Unknown delivery outcome", request=request)

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw),
    )
    state.observe("first")
    clock.return_value += 1800
    with tool_invocation(invocation):
        assert await context.tools["send_idle"].fn("first", "A friendly question?") == {
            "status": "superseded"
        }
    assert not posts
    clock.return_value += 7200
    with tool_invocation(invocation):
        with pytest.raises(httpx.ReadTimeout):
            await context.tools["send_idle"].fn("new-human-message", "Another question?")
        assert await context.tools["send_idle"].fn("new-human-message", "Another question?") == {
            "status": "skipped"
        }
    assert len(posts) == 1


@pytest.mark.asyncio
async def test_activity_push_uses_verified_destination_and_durable_request_key(
    activity, monkeypatch
):
    import json

    module, context, state, clock, invocation = activity
    original_client = httpx.AsyncClient
    receipt, payloads = uuid4(), []

    def respond(request):
        assert (
            request.headers["X-Session-ID"] == invocation.credentials["session"].get_secret_value()
        )
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "receipt_id": str(receipt),
                        "conversation": {"external_id": "verified-group", "kind": "group"},
                    }
                ],
            )
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"deliveries": [str(uuid4())]})

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw),
    )
    state.observe("first")
    clock.return_value += 1800
    with tool_invocation(invocation):
        assert await context.tools["send_idle"].fn("first", "A friendly question?") == {
            "status": "queued"
        }
        assert await context.tools["send_idle"].fn("first", "A duplicate?") == {"status": "skipped"}
    assert len(payloads) == 1
    assert payloads[0]["recipients"] == [str(receipt)]
    assert payloads[0]["content"] == [{"type": "text", "text": "A friendly question?"}]
