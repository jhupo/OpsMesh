"""Explicit group admission delegates one restricted identity without enrolling members."""

import httpx
import pytest
from pydantic import ValidationError

from opsmesh.framework.drivers import OpsMeshDriver
from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.message import DispatchResult
from opsmesh.infrastructure.bots.qq import QQAdapter
from opsmesh.modules.accounts.api import AccessRole, PermissionGrant
from opsmesh.modules.bots.api import BotAccount

from .fixtures import account, webhook
from .helpers import receive_qq_event
from .test_authorization_boundaries import platform as platform


@pytest.fixture
async def shared_platform(platform):
    service, accounts, users, resolved, _dispatched = platform
    await accounts.create_role(
        AccessRole(
            name="group_receiver",
            grants=(PermissionGrant(action="bots.receive", resource="support"),),
        )
    )
    for name in ("alice", "bob"):
        await accounts.update_user(users[name].id, roles=("group_receiver",))
    contexts = []

    async def dispatch(context):
        contexts.append(context)
        return DispatchResult(context.event.id)

    service.ingress.dispatcher = dispatch
    return service, accounts, users, resolved, contexts


async def configure_shared(service, users, *, principal="alice", channels=("group:300",)):
    current = await service.get_account("support", actor_id=users["admin"].id)
    configured = current.model_copy(
        update={
            "unlinked_principal_id": users[principal].id if principal else None,
            "unlinked_channels": channels,
        }
    )
    return await service.update_account(
        configured, expected_revision=current.revision, actor_id=users["admin"].id
    )


async def receive_group(service, **overrides):
    body, signature = webhook(**{"message_type": "group", "group_id": 300, **overrides})
    return await receive_qq_event(service, "support", body, signature=signature)


@pytest.mark.parametrize(
    "policy",
    [
        {"unlinked_principal_id": "service"},
        {"unlinked_channels": ("group:300",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("*",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("group:*",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("group:3?0",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("group:[300]",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("group:",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("c2c:200",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("group:30 0",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("group:300\n",)},
        {"unlinked_principal_id": "service", "unlinked_channels": ("group:300", "group:300")},
        {"unlinked_principal_id": " service", "unlinked_channels": ("group:300",)},
    ],
)
def test_shared_policy_requires_paired_exact_group_configuration(policy):
    with pytest.raises(ValidationError):
        BotAccount.model_validate({**account().model_dump(), **policy})


async def test_platform_admission_fields_remain_compatible_with_qq_adapter_configuration():
    configured = BotAccount(
        id="qq-community",
        self_id="100",
        adapter="qq",
        settings={"app_id": "100"},
        credentials={"client_secret": "env:QQ_TEST_SECRET"},
        unlinked_principal_id="group-service",
        unlinked_channels=("group:known-group-openid",),
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    ) as client:
        adapter = QQAdapter(
            OpsMeshDriver(), client, lambda reference: "test-secret", autostart=False
        )
        adapter.configure(configured)
        assert adapter.get_bot(configured).configuration == configured
        await adapter.close()


async def test_default_policy_denies_unknown_group_members(shared_platform):
    service, _accounts, users, _resolved, contexts = shared_platform
    current = await service.get_account("support", actor_id=users["admin"].id)
    assert current.unlinked_principal_id is None and current.unlinked_channels == ()
    with pytest.raises(OpsMeshError, match="not linked") as denied:
        await receive_group(service)
    assert denied.value.code == "FORBIDDEN"
    assert contexts == []


async def test_shared_group_keeps_external_sender_without_identity_or_privilege_creation(
    shared_platform,
):
    service, accounts, users, _resolved, contexts = shared_platform
    original_users = await accounts.list_users()
    await configure_shared(service, users)
    receipt = await receive_group(service, principal_id=users["admin"].id)
    assert receipt.status == "processed"
    assert contexts[0].event.user_id == "200"
    assert contexts[0].event.channel_id == "group:300"
    assert contexts[0].values["principal_id"] == users["alice"].id
    assert contexts[0].envelope.actor_ref == users["alice"].id
    assert await service.list_identities("support", actor_id=users["admin"].id) == []
    assert await accounts.list_users() == original_users
    principal = await accounts.get_principal(users["alice"].id)
    assert not principal.is_admin
    for action in ("bots.manage", "accounts:user:manage", "agent:run"):
        with pytest.raises(OpsMeshError) as denied:
            await accounts.authorize(principal, action, "support")
        assert denied.value.code == "FORBIDDEN"


@pytest.mark.parametrize("overrides", [{"group_id": 301}, {"message_type": "private"}])
async def test_shared_identity_cannot_admit_other_groups_or_private_messages(
    shared_platform, overrides
):
    service, _accounts, users, _resolved, contexts = shared_platform
    await configure_shared(service, users)
    with pytest.raises(OpsMeshError) as denied:
        await receive_group(service, **overrides)
    assert denied.value.code == "FORBIDDEN" and contexts == []


async def test_manual_mapping_takes_priority_and_denial_never_falls_back(shared_platform):
    service, accounts, users, _resolved, contexts = shared_platform
    await configure_shared(service, users)
    await service.bind_identity("support", "200", users["bob"].id, actor_id=users["admin"].id)
    await receive_group(service)
    assert contexts[0].values["principal_id"] == users["bob"].id
    await accounts.update_user(users["bob"].id, roles=())
    with pytest.raises(OpsMeshError) as denied:
        await receive_group(service, message_id=2)
    assert denied.value.code == "FORBIDDEN" and len(contexts) == 1


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("principal", ["alice", "admin"])
async def test_management_permission_alone_cannot_delegate_shared_identity(
    shared_platform, operation, principal
):
    service, _accounts, users, resolved, _contexts = shared_platform
    current = await service.get_account("support", actor_id=users["admin"].id)
    attempted = current.model_copy(
        update={"unlinked_principal_id": users[principal].id, "unlinked_channels": ("group:300",)}
    )
    with pytest.raises(OpsMeshError) as denied:
        if operation == "create":
            await service.create_account(attempted, actor_id=users["operator"].id)
        else:
            await service.update_account(
                attempted, expected_revision=current.revision, actor_id=users["operator"].id
            )
    assert denied.value.code == "FORBIDDEN"
    assert resolved == []
    assert await service.get_account("support", actor_id=users["admin"].id) == current


@pytest.mark.parametrize("invalid", ["admin", "inactive", "no_receive"])
async def test_shared_target_must_be_active_restricted_and_authorized(shared_platform, invalid):
    service, accounts, users, resolved, _contexts = shared_platform
    principal = "admin" if invalid == "admin" else "alice"
    if invalid == "inactive":
        await accounts.update_user(users[principal].id, active=False)
    elif invalid == "no_receive":
        await accounts.update_user(users[principal].id, roles=())
    with pytest.raises(OpsMeshError) as denied:
        await configure_shared(service, users, principal=principal)
    assert denied.value.code == "FORBIDDEN"
    assert resolved == []
    assert (await service.get_account("support", actor_id=users["admin"].id)).revision == 1


async def test_replacing_or_removing_shared_identity_requires_previous_delegation(shared_platform):
    service, _accounts, users, _resolved, _contexts = shared_platform
    current = await configure_shared(service, users, principal="bob")
    for principal, channels in ((users["alice"].id, ("group:300",)), (None, ())):
        attempted = current.model_copy(
            update={"unlinked_principal_id": principal, "unlinked_channels": channels}
        )
        with pytest.raises(OpsMeshError) as denied:
            await service.update_account(
                attempted, expected_revision=current.revision, actor_id=users["binder"].id
            )
        assert denied.value.code == "FORBIDDEN"
    assert await service.get_account("support", actor_id=users["admin"].id) == current


async def test_new_shared_identity_also_requires_delegation(shared_platform):
    service, _accounts, users, _resolved, _contexts = shared_platform
    current = await configure_shared(service, users)
    attempted = current.model_copy(update={"unlinked_principal_id": users["bob"].id})
    with pytest.raises(OpsMeshError) as denied:
        await service.update_account(
            attempted, expected_revision=current.revision, actor_id=users["binder"].id
        )
    assert denied.value.code == "FORBIDDEN"


@pytest.mark.parametrize("revocation", ["inactive", "no_receive", "admin"])
async def test_shared_identity_status_is_rechecked_for_every_message(shared_platform, revocation):
    service, accounts, users, _resolved, contexts = shared_platform
    await configure_shared(service, users)
    await receive_group(service)
    if revocation == "inactive":
        await accounts.update_user(users["alice"].id, active=False)
    elif revocation == "no_receive":
        await accounts.update_user(users["alice"].id, roles=())
    else:
        # The account API intentionally cannot promote users; simulate a privileged migration.
        record = await accounts.users.get(users["alice"].id)
        assert record is not None
        await accounts.users.update(record.id, {**record.data, "is_admin": True}, record.revision)
    with pytest.raises(OpsMeshError) as denied:
        await receive_group(service, message_id=2)
    assert denied.value.code == "FORBIDDEN" and len(contexts) == 1


@pytest.mark.parametrize("change", ["remove_group", "remove_policy", "disable"])
async def test_updated_policy_revokes_future_group_admission(shared_platform, change):
    service, _accounts, users, _resolved, contexts = shared_platform
    previous = await configure_shared(service, users)
    await receive_group(service)
    if change == "remove_group":
        await configure_shared(service, users, channels=("group:301",))
    elif change == "remove_policy":
        await configure_shared(service, users, principal=None, channels=())
    else:
        await service.set_enabled("support", False, actor_id=users["admin"].id)
    with pytest.raises(OpsMeshError) as denied:
        await receive_group(service, message_id=2)
    assert denied.value.code == ("BOT_DISABLED" if change == "disable" else "FORBIDDEN")
    with pytest.raises(OpsMeshError) as stale:
        await service._current_connection(previous)
    assert stale.value.code == ("BOT_DISABLED" if change == "disable" else "CONFLICT")
    assert len(contexts) == 1


async def test_revoked_shared_user_does_not_prevent_disabling_but_prevents_reenable(
    shared_platform,
):
    service, accounts, users, _resolved, _contexts = shared_platform
    await configure_shared(service, users)
    await accounts.update_user(users["alice"].id, active=False)
    disabled = await service.set_enabled("support", False, actor_id=users["admin"].id)
    assert not disabled.enabled
    with pytest.raises(OpsMeshError) as denied:
        await service.set_enabled("support", True, actor_id=users["admin"].id)
    assert denied.value.code == "FORBIDDEN"


async def test_shared_delegation_protects_route_and_manual_mapping_mutations(shared_platform):
    service, _accounts, users, _resolved, _contexts = shared_platform
    await configure_shared(service, users, principal="bob")
    await service.bind_route("support", "group:300", "controlled@1", actor_id=users["admin"].id)
    await service.bind_identity("support", "200", users["alice"].id, actor_id=users["admin"].id)
    for operation in (
        service.bind_route("support", "group:300", "controlled@1", actor_id=users["binder"].id),
        service.unbind_route("support", "group:300", actor_id=users["binder"].id),
        service.bind_identity("support", "201", users["alice"].id, actor_id=users["binder"].id),
        service.unbind_identity("support", "200", actor_id=users["binder"].id),
    ):
        with pytest.raises(OpsMeshError) as denied:
            await operation
        assert denied.value.code == "FORBIDDEN"


async def test_scoped_delegate_can_enable_and_remove_its_shared_identity(shared_platform):
    service, _accounts, users, _resolved, contexts = shared_platform
    current = await service.get_account("support", actor_id=users["admin"].id)
    configured = current.model_copy(
        update={"unlinked_principal_id": users["alice"].id, "unlinked_channels": ("group:300",)}
    )
    updated = await service.update_account(
        configured, expected_revision=current.revision, actor_id=users["binder"].id
    )
    await receive_group(service)
    assert contexts[0].values["principal_id"] == users["alice"].id
    await service.update_account(
        updated.model_copy(update={"unlinked_principal_id": None, "unlinked_channels": ()}),
        expected_revision=updated.revision,
        actor_id=users["binder"].id,
    )
    with pytest.raises(OpsMeshError) as denied:
        await receive_group(service, message_id=2)
    assert denied.value.code == "FORBIDDEN"
