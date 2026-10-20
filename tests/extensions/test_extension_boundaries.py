"""Hook policy and SDK production contracts without substituted business owners."""

import asyncio
import threading

import pytest
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.manifest import PackageManifest

from opsmesh.extensions.delegation import current_call
from opsmesh.extensions.hooks import apply_chain
from opsmesh.extensions.managed_activities import complete_process_effect
from opsmesh.identity.errors import AuthenticationError
from opsmesh.shared.errors import ForbiddenError


@pytest.mark.parametrize("effect_fails", [False, True])
def test_cancel_waits_for_submitted_process_effect_before_later_cleanup(effect_fails):
    async def verify():
        entered = threading.Event()
        finish = threading.Event()
        completed = []

        def effect():
            entered.set()
            assert finish.wait(5)
            completed.append("effect_finished")
            if effect_fails:
                raise RuntimeError("Docker operation failed")
            return "container"

        pending = asyncio.create_task(complete_process_effect(effect))
        assert await asyncio.to_thread(entered.wait, 5)
        try:
            pending.cancel()
            await asyncio.sleep(0.02)
            pending.cancel()
            await asyncio.sleep(0.02)
            assert not pending.done()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 5)
        assert completed == ["effect_finished"]

    asyncio.run(verify())


def binding(identifier, *, order=0, stages=None, policy="block", patches=None):
    return {
        "binding_id": identifier,
        "order": order,
        "stages": stages or ["model.before", "model.after"],
        "failure_policy": policy,
        "patch_fields": patches or [],
    }


def test_before_after_order_and_only_entered_pairs():
    seen = []
    bindings = [binding("b", order=2), binding("a", order=1), binding("c", order=3)]

    async def invoke(item, arguments, operation_id):
        seen.append((item["binding_id"], arguments["stage"], operation_id))
        return {"decision": "continue"}

    async def verify():
        before = await apply_chain(
            stage="model.before",
            occurrence="model:1",
            payload={},
            bindings=bindings[:2],
            invoke=invoke,
        )
        await apply_chain(
            stage="model.after",
            occurrence="model:1",
            payload={},
            bindings=bindings,
            invoke=invoke,
            entered=before["entered"],
        )
        assert [item[:2] for item in seen] == [
            ("a", "model.before"),
            ("b", "model.before"),
            ("b", "model.after"),
            ("a", "model.after"),
        ]
        assert len({item[2] for item in seen}) == 4

    asyncio.run(verify())


@pytest.mark.parametrize("field", ["principal_id", "workspace_id", "usage", "approval", "effects"])
def test_hook_cannot_patch_authority_or_confirmed_effects(field):
    async def invoke(item, arguments, operation_id):
        return {"decision": "patch", "fields": {field: "forged"}}

    with pytest.raises(ForbiddenError):
        asyncio.run(
            apply_chain(
                stage="model.before",
                occurrence="model:1",
                payload={},
                bindings=[binding("a", patches=[field])],
                invoke=invoke,
            )
        )


def test_unknown_failure_is_only_ignored_for_explicit_observer():
    async def invoke(item, arguments, operation_id):
        raise RuntimeError("unavailable")

    observer = asyncio.run(
        apply_chain(
            stage="model.before",
            occurrence="model:1",
            payload={},
            bindings=[binding("a", policy="observe")],
            invoke=invoke,
        )
    )
    assert observer["payload"] == {}
    with pytest.raises(RuntimeError):
        asyncio.run(
            apply_chain(
                stage="model.before",
                occurrence="model:1",
                payload={},
                bindings=[binding("a")],
                invoke=invoke,
            )
        )


def test_public_identifiers_never_establish_delegation():
    with pytest.raises(AuthenticationError):
        current_call()


def test_manifest_rejects_unknown_implementation_and_sdk_facade_has_no_fake_session():
    with pytest.raises(ValueError):
        PackageManifest(
            package_name="demo", version="1", description="Demo", execution={"mode": "host"}
        )

    class BorrowedTransport:
        pass

    client = PlatformClient("", transport=BorrowedTransport())
    assert client.context().identity_headers == {}
