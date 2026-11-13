"""Verify official platform operation mappings using isolated transports."""

import json
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from opsmesh.plugins.channels import (
    OperationPermissionDenied,
    OperationRateLimited,
)
from opsmesh.plugins.channels.dingtalk.client import DingTalkClient
from opsmesh.plugins.channels.dingtalk.config import (
    DingTalkConnectionSettings,
    DingTalkSettings,
)
from opsmesh.plugins.channels.dingtalk.groups import DingTalkGroups
from opsmesh.plugins.channels.operations import operation_response
from opsmesh.plugins.channels.qq.client import QQClient
from opsmesh.plugins.channels.qq.config import QQConnectionSettings, QQSettings
from opsmesh.plugins.channels.qq.events import QQNoticeEvent, parse_callback
from opsmesh.plugins.channels.qq.groups import QQGroups
from opsmesh.plugins.model import Plugin, PluginStatus
from opsmesh.plugins.registry import plugin_scope


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation,arguments,method,path,body",
    [
        ("get_guild", {"guild_id": "g"}, "GET", "/guilds/g", None),
        (
            "get_guild_member",
            {"guild_id": "g", "user_id": "u"},
            "GET",
            "/guilds/g/members/u",
            None,
        ),
        (
            "get_guild_members",
            {"guild_id": "g", "after": "last", "limit": 2},
            "GET",
            "/guilds/g/members",
            None,
        ),
        ("get_guild_roles", {"guild_id": "g"}, "GET", "/guilds/g/roles", None),
        (
            "mute_member",
            {"guild_id": "g", "user_id": "u", "mute_seconds": 30},
            "PATCH",
            "/guilds/g/members/u/mute",
            b'{"mute_seconds":"30"}',
        ),
        (
            "unmute_member",
            {"guild_id": "g", "user_id": "u"},
            "PATCH",
            "/guilds/g/members/u/mute",
            b'{"mute_seconds":"0"}',
        ),
        (
            "mute_all",
            {"guild_id": "g", "mute_seconds": 30},
            "PATCH",
            "/guilds/g/mute",
            b'{"mute_seconds":"30"}',
        ),
        (
            "unmute_all",
            {"guild_id": "g"},
            "PATCH",
            "/guilds/g/mute",
            b'{"mute_seconds":"0"}',
        ),
        (
            "get_delete_member",
            {"guild_id": "g", "user_id": "u"},
            "DELETE",
            "/guilds/g/members/u",
            b'{"add_blacklist":false,"delete_history_msg_days":0}',
        ),
    ],
)
async def test_qq_guild_operation_wire_contract(
    operation, arguments, method, path, body
):
    requests = []

    def respond(request):
        if request.url.host == "bots.qq.com":
            return httpx.Response(
                200, json={"access_token": "token", "expires_in": 7200}
            )
        requests.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        settings = QQSettings()
        account = SimpleNamespace(
            self_id="owner:qq",
            settings=QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET"),
            protocol=settings,
        )
        groups = QQGroups(QQClient(settings, http), account, "secret")
        assert await getattr(groups, operation)(**arguments) is None
    request = requests[0]
    assert (request.method, request.url.path) == (method, path)
    assert request.headers["authorization"] == "QQBot token"
    if body is not None:
        assert request.content == body
    if operation == "get_guild_members":
        assert dict(request.url.params) == {"after": "last", "limit": "2"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "operation,arguments,method,path",
    [
        (
            "query_open_group_base_info",
            {"open_conversation_id": "cid"},
            "POST",
            "/v1.0/im/groups/baseInfos/query",
        ),
        (
            "query_group_member",
            {"open_conversation_id": "cid"},
            "GET",
            "/v1.0/im/interconnections/conversations/members",
        ),
        (
            "update_group_name",
            {"open_conversation_id": "cid", "group_name": "Team"},
            "PUT",
            "/v1.0/im/interconnections/groups/names",
        ),
        (
            "add_group_member",
            {
                "open_conversation_id": "cid",
                "operator_id": "owner",
                "user_ids": ["u"],
            },
            "POST",
            "/v1.0/im/interconnections/groups/members",
        ),
        (
            "remove_group_member",
            {
                "open_conversation_id": "cid",
                "operator_id": "owner",
                "user_ids": ["u"],
            },
            "POST",
            "/v1.0/im/interconnections/groups/members/remove",
        ),
    ],
)
async def test_dingtalk_group_operation_wire_contract(
    monkeypatch, operation, arguments, method, path
):
    monkeypatch.setenv("DT_SECRET", "secret")
    requests = []

    def respond(request):
        if request.url.path.endswith("accessToken"):
            return httpx.Response(200, json={"accessToken": "token"})
        requests.append(request)
        return httpx.Response(200, json={"success": True})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(respond)
    ) as http:
        client = DingTalkClient(DingTalkSettings(), http)
        account = SimpleNamespace(
            settings=DingTalkConnectionSettings(
                client_id="app", client_secret_ref="DT_SECRET"
            )
        )
        assert await getattr(DingTalkGroups(client, account), operation)(
            **arguments
        ) == {"success": True}
    request = requests[0]
    assert (request.method, request.url.path) == (method, path)
    assert request.headers["x-acs-dingtalk-access-token"] == "token"
    if operation == "query_group_member":
        assert dict(request.url.params) == {"openConversationId": "cid"}
    else:
        import json

        body = json.loads(request.content)
        assert body["openConversationId"] == "cid"
        if "user_ids" in arguments:
            assert (body["operatorId"], body["userIds"]) == ("owner", ["u"])


@pytest.mark.parametrize(
    "status,error_type",
    [(403, OperationPermissionDenied), (429, OperationRateLimited)],
)
def test_platform_failure_preserves_safe_code_without_body(status, error_type):
    with pytest.raises(error_type) as failure:
        operation_response(
            httpx.Response(
                status,
                json={"code": "Forbidden", "message": "secret"},
                headers={"x-acs-request-id": "request-1"},
            )
        )
    assert failure.value.code == "Forbidden"
    assert failure.value.request_id == "request-1"
    assert "secret" not in str(failure.value)


def test_qq_notice_preserves_subject_without_inventing_actor():
    event = parse_callback(
        {
            "t": "GUILD_MEMBER_ADD",
            "id": "evt",
            "d": {"guild_id": "g", "user": {"id": "u"}},
        },
        connection_id="bot",
    )
    assert isinstance(event, QQNoticeEvent)
    assert (event.get_type(), event.subject_id, event.operator_id) == (
        "notice",
        "u",
        None,
    )
    with pytest.raises(ValueError, match="actor"):
        event.get_user_id()


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["qq", "dingtalk"])
async def test_group_operations_reject_foreign_connection_owner(protocol):
    from opsmesh.config import Config, Env
    from opsmesh.drivers import Driver
    from opsmesh.plugins.channels.dingtalk import DingTalkAdapter
    from opsmesh.plugins.channels.qq import QQAdapter

    owner = Plugin(
        name="owner",
        module=ModuleType("owner"),
        module_name="owner",
        manager=None,
        status=PluginStatus.READY,
    )
    other = Plugin(
        name="other",
        module=ModuleType("other"),
        module_name="other",
        manager=None,
        status=PluginStatus.READY,
    )
    driver = Driver(Env(), Config())
    if protocol == "qq":
        adapter = QQAdapter(driver, settings=QQSettings())
        settings = QQConnectionSettings(app_id="123", secret_ref="QQ_SECRET")
        operation, arguments = "get_guild", {"guild_id": "g"}
    else:
        adapter = DingTalkAdapter(driver, settings=DingTalkSettings())
        settings = DingTalkConnectionSettings(
            client_id="app", client_secret_ref="DT_SECRET"
        )
        operation, arguments = (
            "query_open_group_base_info",
            {"open_conversation_id": "cid"},
        )
    with plugin_scope(owner):
        channel = adapter.declare(settings, name="bot")
    channel.mark_ready()
    with (
        plugin_scope(other),
        pytest.raises(PermissionError, match="another plugin"),
    ):
        await adapter._call(channel, operation, **arguments)


@pytest.mark.anyio
async def test_dingtalk_native_event_uses_notice_queue_and_sdk_ack(monkeypatch):
    from opsmesh.config import Config, Env
    from opsmesh.drivers import Driver
    from opsmesh.plugins.channels.dingtalk import DingTalkAdapter
    from opsmesh.plugins.channels.dingtalk.events import DingTalkNoticeEvent
    from opsmesh.plugins.channels.dingtalk.stream import DingTalkStream

    adapter = DingTalkAdapter(
        Driver(Env(), Config()), settings=DingTalkSettings()
    )
    owner = Plugin(
        name="owner",
        module=ModuleType("owner"),
        module_name="owner",
        manager=None,
        status=PluginStatus.READY,
    )
    with plugin_scope(owner):
        channel = adapter.declare(
            DingTalkConnectionSettings(
                client_id="app", client_secret_ref="DT_SECRET"
            ),
            name="bot",
        )
    channel.mark_ready()
    adapter._accepting = True
    queued, acknowledgements = [], []

    def enqueue(item):
        queued.append(item)
        return True

    class Socket:
        async def send(self, frame):
            acknowledgements.append(json.loads(frame))

    monkeypatch.setattr(adapter._queue, "put_nowait", enqueue)
    stream = DingTalkStream(channel, "secret")
    stream.websocket = Socket()
    await stream.route_message(
        {
            "type": "EVENT",
            "headers": {
                "messageId": "frame",
                "eventId": "evt",
                "eventType": "chat_update_title",
            },
            "data": json.dumps({"chatId": "chat", "operator": "user"}),
        }
    )
    event = queued[0].event
    assert isinstance(event, DingTalkNoticeEvent)
    assert (event.get_type(), event.event_type, event.get_session_id()) == (
        "notice",
        "chat_update_title",
        f"{channel.self_id}:chat:chat",
    )
    assert queued[0].caller is None
    assert acknowledgements[0]["headers"]["messageId"] == "frame"
