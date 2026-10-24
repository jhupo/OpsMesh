"""Plugin admission, current-message input and independent dispatch regressions."""

import asyncio
import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from opsmesh_sdk import PluginContext, PluginManifest
from opsmesh_sdk.channels import ContentPart
from opsmesh_sdk.messages import MessageFilterResult
from opsmesh_sdk.registration import InvocationContext, get_invocation, tool_invocation
from pydantic import SecretStr, ValidationError

from opsmesh.gateway.channels.manager import Gateway
from opsmesh.gateway.channels.policy import GatewayPolicy
from opsmesh.gateway.contracts import BatchMessage, ChannelDefinition, ChannelTarget, GatewayTurn
from opsmesh.gateway.messaging.dispatcher import Dispatcher
from opsmesh.gateway.messaging.filters import FilterDecision, MessageFilterAdapter, MessageFilters
from opsmesh.gateway.messaging.inputs import native_messages
from opsmesh.shared.errors import InvalidArgumentError
from tests.gateway.test_policy import message


@pytest.mark.asyncio
async def test_rejection_response_bypasses_the_chat_agent(tmp_path):
    current = turn()
    result = MessageFilterResult(action="drop", rejection_input="Generate a brief rejection.")
    inbox = Mock(turn=Mock(return_value=(current, None)))
    outbox = Mock()
    execution = Mock(authorize=AsyncMock(), submit=AsyncMock())
    filters = Mock(evaluate=AsyncMock(return_value=FilterDecision(result)))
    await Dispatcher(inbox, outbox, execution, tmp_path, filters).process(
        current.inbox_id, current.lease_owner
    )
    outbox.reject.assert_called_once_with(current, result, None)
    execution.submit.assert_not_awaited()
    inbox.approved.assert_not_called()


@pytest.fixture(autouse=True)
def isolated_media(monkeypatch):
    # These are scheduling/admission unit tests; POSIX file storage has separate tests.
    monkeypatch.setattr("opsmesh.gateway.messaging.filters.GatewayMedia", Mock())
    monkeypatch.setattr("opsmesh.gateway.channels.manager.GatewayMedia", Mock())


def turn() -> GatewayTurn:
    channel, identity, user = uuid4(), uuid4(), uuid4()
    return GatewayTurn(
        inbox_id=uuid4(),
        channel_id=channel,
        identity_id=identity,
        lease_owner="worker",
        user_id=user,
        message=message(visibility="all"),
        target=ChannelTarget(
            user_id=user,
            target_kind="agent",
            target_id=uuid4(),
        ),
        policy=GatewayPolicy(message_filters=["review/message_filter/check"]),
    )


@pytest.mark.asyncio
async def test_stopping_a_channel_does_not_require_its_revoked_target_access(tmp_path):
    target = turn().target
    previous = ChannelDefinition(
        id=uuid4(), owner_id=uuid4(), kind="qq", name="Channel", target=target, enabled=True
    )
    disabled = previous.model_copy(update={"enabled": False})
    store = Mock(get=Mock(return_value=previous), save=Mock(return_value=disabled))
    execution = Mock(authorize=AsyncMock(side_effect=InvalidArgumentError("Revoked")))
    gateway = Gateway(registry=Mock(), store=store, media_root=tmp_path, execution=execution)
    assert await gateway.save(disabled, previous.owner_id) == disabled
    execution.authorize.assert_not_awaited()
    with pytest.raises(InvalidArgumentError, match="Revoked"):
        await gateway.save(previous, previous.owner_id)
    changed = disabled.model_copy(
        update={"target": target.model_copy(update={"target_id": uuid4()})}
    )
    with pytest.raises(InvalidArgumentError, match="Revoked"):
        await gateway.save(changed, previous.owner_id)


@pytest.mark.asyncio
async def test_filter_uses_trusted_context_and_fails_closed_when_unavailable(tmp_path):
    current = turn()
    actor = SimpleNamespace(
        user_id=current.user_id, platform_roles=frozenset(), has_platform_role=lambda name: False
    )
    context = PluginContext(
        manifest=PluginManifest(id="review", version="0.1.0", name="Review", kind="message_filter"),
        source_path=tmp_path,
        data_directory=tmp_path / "data",
    )
    context.credential("api_key", "env:TEST_KEY")

    async def check(original, media):
        invocation = get_invocation()
        assert original is current.message
        assert invocation.user_id == current.user_id and not invocation.is_admin
        assert invocation.source.external_id == "member"
        assert invocation.credentials["api_key"].get_secret_value() == "test-only"
        return MessageFilterResult(action="allow", scores={"spam": 0.01})

    context.message_filter("check", check)
    resource = MessageFilterAdapter().prepare(context, "check", context.message_filters["check"])
    authorize, credentials = (
        AsyncMock(return_value=actor),
        AsyncMock(return_value=SecretStr("test-only")),
    )
    resources = {resource.id: resource}
    filters = MessageFilters(lambda: resources, credentials, authorize, tmp_path)
    assert (await filters.evaluate(current)).result.scores == {"spam": 0.01}
    with pytest.raises(LookupError):
        get_invocation()
    resources.clear()
    with pytest.raises(InvalidArgumentError, match="unavailable"):
        await filters.evaluate(current)


@pytest.mark.asyncio
async def test_filter_timeout_includes_credentials_and_propagates_cancellation(tmp_path):
    current = turn()
    context = PluginContext(
        manifest=PluginManifest(id="review", version="0.1.0", name="Review", kind="message_filter"),
        source_path=tmp_path,
        data_directory=tmp_path / "data",
    )
    handler = AsyncMock(return_value=MessageFilterResult(action="allow"))
    context.message_filter("check", handler, timeout_seconds=0.01)
    context.credential("api_key", "env:TEST_KEY")
    resource = MessageFilterAdapter().prepare(context, "check", context.message_filters["check"])

    async def slow(reference):
        await asyncio.Event().wait()

    filters = MessageFilters(lambda: {resource.id: resource}, slow, AsyncMock(), tmp_path)
    with pytest.raises(TimeoutError):
        await filters.evaluate(current)
    handler.assert_not_awaited()
    cancelled = asyncio.create_task(filters.evaluate(current))
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled


@pytest.mark.asyncio
async def test_native_input_keeps_original_senders_without_injecting_platform_history():
    current = turn()
    original = current.message.model_copy(update={"content": [ContentPart(text="First")]})
    following = original.model_copy(
        update={
            "event_id": "second",
            "actor": original.actor.model_copy(update={"external_id": "other"}),
            "content": [ContentPart(type="image", media_key="image", mime_type="image/png")],
        }
    )
    current = current.model_copy(
        update={
            "batch": [
                BatchMessage(
                    inbox_id=current.inbox_id,
                    identity_id=current.identity_id,
                    message=original,
                ),
                BatchMessage(inbox_id=uuid4(), identity_id=uuid4(), message=following),
            ]
        }
    )
    media = Mock(
        prepare=AsyncMock(side_effect=lambda parts: parts), read=AsyncMock(return_value=b"png")
    )
    inputs = await native_messages(current, media)
    assert len(inputs) == 2
    assert json.loads(inputs[0]["content"][0]["text"])["text"] == ["First"]
    facts = json.loads(inputs[1]["content"][0]["text"])
    assert facts["sender"]["external_id"] == "other"
    assert "recent_messages" not in facts["context"]
    assert inputs[1]["content"][1]["image_url"] == "data:image/png;base64,cG5n"


@pytest.fixture
def moderation_module():
    path = Path(__file__).parents[2] / "sdk/python/examples/typesafe-decision/main.py"
    spec = importlib.util.spec_from_file_location("moderation_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,action,bypass",
    [("proxy", "allow", True), ("proxy", "block", False), ("direct", "allow", False)],
)
@pytest.mark.parametrize("failure", ["connect", "deadline"])
async def test_moderation_proxy_timeout_requires_explicit_policy(
    moderation_module, monkeypatch, caplog, mode, action, bypass, failure
):
    module, original_client = moderation_module, httpx.AsyncClient
    calls = []

    async def respond(request):
        calls.append(request)
        if failure == "connect":
            raise httpx.ConnectTimeout("Connection timed out", request=request)
        await asyncio.sleep(10)
        raise AssertionError("The deadline must cancel the request")

    def client(**options):
        options.pop("proxy")
        return original_client(transport=httpx.MockTransport(respond), **options)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    settings = module.Settings(
        network_mode=mode,
        proxy_ref="env:TYPESAFE_PROXY_URL" if mode == "proxy" else None,
        proxy_timeout_action=action,
        timeout_seconds=0.05,
    )
    with tool_invocation(
        InvocationContext(
            uuid4(),
            uuid4(),
            False,
            {},
            credentials={
                "api_key": SecretStr("test-only"),
                "proxy": SecretStr("http://127.0.0.1:12334"),
            },
        )
    ):
        if bypass:
            result = await module.moderate(message(), Mock(), settings=settings)
            assert result.action == "allow" and result.reason == "moderation_proxy_timeout"
            assert not result.scores and not result.rejection_input
            assert "configured action is allow" in caplog.text
        else:
            with pytest.raises((TimeoutError, httpx.TimeoutException)):
                await module.moderate(message(), Mock(), settings=settings)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["recall", "http_error", "invalid_response", "cancel"])
async def test_proxy_timeout_policy_preserves_other_moderation_outcomes(
    moderation_module, monkeypatch, outcome
):
    module, original_client = moderation_module, httpx.AsyncClient

    async def respond(request):
        if outcome == "cancel":
            raise asyncio.CancelledError
        if outcome == "http_error":
            return httpx.Response(502)
        if outcome == "invalid_response":
            return httpx.Response(200, json={"answers": {}})
        return httpx.Response(200, json={"answers": {"spam": {"type": "noul", "noul": 0.99}}})

    def client(**options):
        options.pop("proxy")
        return original_client(transport=httpx.MockTransport(respond), **options)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    settings = module.Settings(
        proxy_timeout_action="allow",
        categories={"spam": "Commercial spam"},
        recall_blocked=True,
    )
    expected = {
        "http_error": httpx.HTTPStatusError,
        "invalid_response": ValueError,
        "cancel": asyncio.CancelledError,
    }
    with tool_invocation(
        InvocationContext(
            uuid4(),
            uuid4(),
            False,
            {},
            credentials={
                "api_key": SecretStr("test-only"),
                "proxy": SecretStr("http://127.0.0.1:12334"),
            },
        )
    ):
        if outcome == "recall":
            result = await module.moderate(message(), Mock(), settings=settings)
            assert result.action == "drop" and result.operations and result.reason == "spam"
        else:
            with pytest.raises(expected[outcome]):
                await module.moderate(message(), Mock(), settings=settings)


def test_moderation_rejects_the_removed_action_configuration(moderation_module):
    with pytest.raises(ValidationError):
        moderation_module.Settings(blocked_action="recall")


@pytest.mark.parametrize(
    "overrides",
    [
        {"category_thresholds": {"unknown": 0.6}},
        {"category_thresholds": {"spam": float("nan")}},
        {"category_thresholds": {"spam": 0.4}},
        {"rejection_instructions": " "},
    ],
)
def test_moderation_policy_rejects_invalid_category_overrides(moderation_module, overrides):
    with pytest.raises(ValidationError):
        moderation_module.Settings(categories={"spam": "Commercial spam"}, **overrides)


@pytest.mark.asyncio
@pytest.mark.parametrize("score,expected", [(0.8499, "allow"), (0.85, "drop")])
async def test_all_categories_use_the_requested_threshold(
    moderation_module, monkeypatch, score, expected
):
    module, original_client = moderation_module, httpx.AsyncClient
    settings = module.Settings(
        network_mode="direct",
        proxy_ref=None,
        recall_blocked=True,
        rejection_instructions="Generate a kind, clear rejection.",
    )
    assert settings.threshold == 0.85 and settings.category_thresholds == {}
    categories = {
        "advertising",
        "violence",
        "sexual",
        "self_harm",
        "political",
        "jailbreak",
        "illegal",
        "privacy",
        "unethical",
        "copyright",
    }
    assert set(settings.categories) == categories

    def respond(request):
        body = json.loads(request.content)
        assert set(body["questions"]) == categories
        return httpx.Response(
            200,
            json={
                "answers": {category: {"type": "noul", "noul": score} for category in categories}
            },
        )

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **options: original_client(transport=httpx.MockTransport(respond), **options),
    )
    with tool_invocation(
        InvocationContext(
            uuid4(), uuid4(), False, {}, credentials={"api_key": SecretStr("test-only")}
        )
    ):
        result = await module.moderate(message(), Mock(), settings=settings)
    assert result.action == expected
    if expected == "drop":
        assert set(json.loads(result.rejection_input)["categories"]) == categories
    else:
        assert not result.rejection_input


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,output",
    [
        ("failed", None),
        ("needs_attention", None),
        ("completed", ""),
        ("completed", {"text": "bad"}),
    ],
)
async def test_failed_rejection_generation_never_admits_the_original(tmp_path, status, output):
    current = turn().model_copy(update={"status": "responding"})
    execution = Mock(
        submit=AsyncMock(),
        submit_rejection=AsyncMock(return_value=uuid4()),
        result=AsyncMock(return_value=(status, output)),
    )
    inbox, outbox = Mock(turn=Mock(return_value=(current, None))), Mock()
    await Dispatcher(inbox, outbox, execution, tmp_path).process(
        current.inbox_id, current.lease_owner
    )
    inbox.fail.assert_called_once()
    execution.submit.assert_not_awaited()
    inbox.approved.assert_not_called()
    outbox.complete.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("sexual_score", [0.59, 0.61])
async def test_category_thresholds_select_the_actual_violation_and_rejection(
    moderation_module, monkeypatch, sexual_score
):
    module, original_client = moderation_module, httpx.AsyncClient

    def request_handler(request):
        body = json.loads(request.content)
        assert "requests to generate" in body["questions"]["sexual"]["instructions"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "spam": {"type": "noul", "noul": 0.9},
                    "sexual": {"type": "noul", "noul": sexual_score},
                }
            },
        )

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **options: original_client(
            transport=httpx.MockTransport(request_handler), **options
        ),
    )
    settings = module.Settings(
        network_mode="direct",
        proxy_ref=None,
        categories={"spam": "Commercial spam", "sexual": "Requests for explicit content"},
        threshold=0.95,
        category_thresholds={"sexual": 0.6},
        rejection_instructions="Generate a brief rejection.",
        recall_blocked=True,
    )
    with tool_invocation(
        InvocationContext(
            uuid4(), uuid4(), False, {}, credentials={"api_key": SecretStr("test-only")}
        )
    ):
        result = await module.moderate(message(), Mock(), settings=settings)
    if sexual_score >= 0.6:
        assert result.action == "drop" and result.operations and result.reason == "sexual"
        assert json.loads(result.rejection_input) == {
            "instructions": "Generate a brief rejection.",
            "categories": ["sexual"],
        }
        assert "Current input" not in result.rejection_input
    else:
        assert result.action == "allow" and not result.rejection_input


@pytest.mark.asyncio
async def test_generated_rejection_uses_a_separate_run_without_chat_submission(tmp_path):
    current = turn().model_copy(update={"status": "responding"})
    run_id = uuid4()
    execution = Mock(
        submit_rejection=AsyncMock(return_value=run_id),
        submit=AsyncMock(),
        result=AsyncMock(return_value=("completed", "Please choose a safe topic.")),
    )
    inbox, outbox = Mock(turn=Mock(return_value=(current, None))), Mock()
    await Dispatcher(inbox, outbox, execution, tmp_path).process(
        current.inbox_id, current.lease_owner
    )
    execution.submit_rejection.assert_awaited_once_with(current)
    execution.submit.assert_not_awaited()
    inbox.approved.assert_not_called()
    outbox.complete.assert_called_once_with(
        current, [ContentPart(text="Please choose a safe topic.")]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_moderation_adapter_checks_current_input_and_validates_response(
    moderation_module, monkeypatch, invalid
):
    module = moderation_module
    original_client = httpx.AsyncClient
    requests = []

    def request_handler(request):
        requests.append(request)
        body = json.loads(request.content)
        assert body["state"]["text"] == ["Current input"]
        assert set(body["questions"]) == {"spam"}
        assert request.headers["Authorization"] == "Bearer test-only"
        return httpx.Response(
            200, json={"answers": {"spam": {"type": "noul", "noul": 1.5 if invalid else 0.99}}}
        )

    def client(**options):
        assert options["proxy"] is None and options["trust_env"] is False
        return original_client(transport=httpx.MockTransport(request_handler), **options)

    monkeypatch.setattr(module.httpx, "AsyncClient", client)
    settings = module.Settings(
        network_mode="direct",
        proxy_ref=None,
        categories={"spam": "Commercial spam"},
        recall_blocked=True,
    )
    current = message().model_copy(update={"content": [ContentPart(text="Current input")]})
    with tool_invocation(
        InvocationContext(
            uuid4(), uuid4(), False, {}, credentials={"api_key": SecretStr("test-only")}
        )
    ):
        if invalid:
            with pytest.raises(ValidationError):
                await module.moderate(current, Mock(), settings=settings)
        else:
            result = await module.moderate(current, Mock(), settings=settings)
            assert result.action == "drop" and result.operations and result.reason == "spam"
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_moderation_images_are_included_and_unreadable_formats_never_pass(
    moderation_module, monkeypatch
):
    module, original_client = moderation_module, httpx.AsyncClient

    def request_handler(request):
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert b'"assets": {"asset-0": {"type": "image", "part": "asset-0"}}' in request.content
        assert b"image-content" in request.content
        return httpx.Response(200, json={"answers": {"spam": {"type": "noul", "noul": 0.01}}})

    monkeypatch.setattr(
        module.httpx,
        "AsyncClient",
        lambda **options: original_client(
            transport=httpx.MockTransport(request_handler), **options
        ),
    )
    settings = module.Settings(network_mode="direct", proxy_ref=None, categories={"spam": "Spam"})
    current = message().model_copy(
        update={
            "content": [
                ContentPart(
                    type="image",
                    data=base64.b64encode(b"image-content").decode(),
                    mime_type="image/png",
                )
            ]
        }
    )
    with tool_invocation(
        InvocationContext(
            uuid4(), uuid4(), False, {}, credentials={"api_key": SecretStr("test-only")}
        )
    ):
        assert (await module.moderate(current, Mock(), settings=settings)).action == "allow"
        unsupported = current.model_copy(
            update={"content": [ContentPart(type="audio", media_key="audio")]}
        )
        with pytest.raises(ValueError, match="cannot inspect"):
            await module.moderate(unsupported, Mock(), settings=settings)


@pytest.mark.asyncio
async def test_dispatch_does_not_wait_for_a_slow_sibling(tmp_path):
    definition = ChannelDefinition(
        id=uuid4(), owner_id=uuid4(), kind="qq", name="Review", policy=GatewayPolicy(workers=2)
    )
    first, second, third = uuid4(), uuid4(), uuid4()
    finished, entered, never = set(), asyncio.Event(), asyncio.Event()
    adapter = Mock(run=AsyncMock(side_effect=never.wait), close=AsyncMock())
    gateway = Gateway(
        registry=Mock(create=Mock(return_value=adapter)),
        store=Mock(),
        execution=Mock(),
        media_root=tmp_path,
    )
    gateway.leases = Mock(acquire=Mock(return_value=True), release=Mock())
    gateway.outbox = Mock(recover=Mock())
    gateway.inbox = Mock(
        pending=Mock(
            side_effect=lambda _: [item for item in (first, second, third) if item not in finished]
        )
    )

    async def process(identifier, owner):
        if identifier == first:
            await never.wait()
        finished.add(identifier)
        if identifier == third:
            entered.set()

    gateway.dispatcher = Mock(process=AsyncMock(side_effect=process))

    async def deliver(*args):
        await never.wait()

    gateway.deliver = AsyncMock(side_effect=deliver)
    task = asyncio.create_task(gateway.channel(definition))
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        assert first not in finished and {second, third} <= finished
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert adapter.close.await_count == 1
