"""Permission-sensitive admission and explicit control-output contracts."""

from uuid import uuid4

import pytest
from opsmesh_sdk.channels import ContentPart, Conversation, ExternalActor, InboundMessage
from pydantic import ValidationError

from opsmesh.gateway.channels.policy import GatewayPolicy, GroupPolicy
from opsmesh.gateway.contracts import GatewayReply, GatewayTurn, PushCreate, RouteDefinition
from opsmesh.gateway.messaging.dispatcher import reply_parts
from opsmesh.gateway.messaging.outbox import reconcile_stream_text


def message(*, mentioned: bool = False, visibility: str = "unknown") -> InboundMessage:
    return InboundMessage.model_validate(
        {
            "event_id": "event",
            "actor": {"scope": "app", "id_kind": "member_openid", "external_id": "member"},
            "conversation": {"kind": "group", "external_id": "group"},
            "content": [{"text": "Hello"}],
            "context": {"bot_mentioned": mentioned, "visibility": visibility},
        }
    )


def test_configuration_cannot_grant_unobserved_all_message_access() -> None:
    with pytest.raises(ValidationError):
        GroupPolicy(activation="always")
    policy = GatewayPolicy(group=GroupPolicy(visibility="all", activation="always"))
    assert not policy.activates(message(visibility="unknown"))
    assert not policy.activates(message(visibility="mention_only"))
    assert policy.activates(message(visibility="all"))


def test_group_admission_requires_a_bot_mention_by_default() -> None:
    policy = GatewayPolicy()
    assert not policy.activates(message(visibility="all"))
    assert policy.activates(message(mentioned=True))
    private = message().model_copy(
        update={"conversation": Conversation(kind="private", external_id="member")}
    )
    assert not GatewayPolicy(private_enabled=False).activates(private)


def test_only_explicit_typed_routes_accept_reply_control_values() -> None:
    assert GatewayReply.parse({"action": "ignore", "content": []}).content == []
    with pytest.raises(ValueError):
        GatewayReply.parse({"action": "ignore", "content": [{"text": "Hidden output"}]})
    with pytest.raises(ValueError):
        GatewayReply.parse({"action": "reply", "content": []})
    with pytest.raises(ValidationError):
        RouteDefinition(
            id=uuid4(),
            channel_id=uuid4(),
            identity_id=uuid4(),
            target_kind="agent",
            target_id=uuid4(),
            output_mode="message",
        )


def test_session_scope_ignores_display_names_and_changes_with_route_generation() -> None:
    channel_id, identity_id = uuid4(), uuid4()
    turn = GatewayTurn(
        inbox_id=uuid4(),
        lease_owner="worker",
        channel_id=channel_id,
        identity_id=identity_id,
        user_id=uuid4(),
        route=RouteDefinition(
            id=uuid4(),
            channel_id=channel_id,
            identity_id=identity_id,
            target_kind="workflow",
            target_id=uuid4(),
            allow_group=True,
        ),
        message=message(),
    )
    renamed = turn.message.model_copy(
        update={
            "conversation": turn.message.conversation.model_copy(
                update={"name": "New display name"}
            )
        }
    )
    assert (
        turn.source.session_scope
        == turn.model_copy(update={"message": renamed}).source.session_scope
    )
    changed = turn.model_copy(update={"route": turn.route.model_copy(update={"revision": 2})})
    assert turn.source.session_scope != changed.source.session_scope
    other_actor = turn.message.model_copy(
        update={
            "actor": ExternalActor(scope="app", id_kind="member_openid", external_id="different")
        }
    )
    assert (
        turn.source.session_scope
        != turn.model_copy(
            update={"identity_id": uuid4(), "message": other_actor}
        ).source.session_scope
    )


def test_platform_context_respects_expiry_sender_and_duplicate_boundaries() -> None:
    value = message(mentioned=True).model_dump(mode="json")
    value["context"]["recent_messages"] = [
        {
            "message_id": identifier,
            "sender_id": sender,
            "timestamp": sent,
            "content": [{"text": identifier}],
        }
        for identifier, sender, sent in [
            ("expired", "member", 1),
            ("other-user", "other", 999),
            ("retained", "member", 998),
            ("retained", "member", 999),
            ("future", "member", 1001),
        ]
    ]
    incoming = InboundMessage.model_validate(value)
    assert [
        item.message_id
        for item in GatewayPolicy().prepare_context(incoming, 1000).context.recent_messages
    ] == ["retained"]
    assert (
        not GatewayPolicy(group=GroupPolicy(visibility="mention_only"))
        .prepare_context(incoming, 1000)
        .context.recent_messages
    )
    assert (
        not GatewayPolicy(group=GroupPolicy(history_limit=0))
        .prepare_context(incoming, 1000)
        .context.recent_messages
    )


def test_broadcast_and_explicit_recipients_cannot_be_combined() -> None:
    content = [ContentPart(text="Update")]
    assert PushCreate(request_id=uuid4(), broadcast=True, content=content).broadcast
    with pytest.raises(ValidationError):
        PushCreate(request_id=uuid4(), content=content)
    with pytest.raises(ValidationError):
        PushCreate(request_id=uuid4(), recipients=[uuid4()], broadcast=True, content=content)


def test_replace_stream_preserves_accepted_text_and_limits_final_size() -> None:
    assert reconcile_stream_text("Hello", "Hello world") == "Hello world"
    assert reconcile_stream_text("Hello world", "Hello") == "Hello world"
    assert reconcile_stream_text("Hello first", "Hello second") == "Hello firstsecond"
    assert len("".join(part.text for part in reply_parts("a" * 100_001))) == 100_000


def test_media_requires_one_explicit_source() -> None:
    assert ContentPart(type="image", url="https://example.com/image.png").url
    with pytest.raises(ValidationError):
        ContentPart(type="image")
    with pytest.raises(ValidationError):
        ContentPart(type="image", media_key="key", data="eA==")
    with pytest.raises(ValidationError):
        ContentPart(text="Hello", media_key="key")
