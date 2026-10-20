"""Public release moderation and private version boundaries over real PostgreSQL/HTTP."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import UUID

import httpx
import pytest
from opsmesh_sdk.api.capabilities import (
    PublicationReviewDecision,
    PublicationReviewSubmission,
    create_capabilities_client,
)
from opsmesh_sdk.client import PlatformClient
from opsmesh_sdk.transport.http import HttpTransport
from sqlalchemy import select

from opsmesh.capabilities import lifecycle
from opsmesh.capabilities.models import CapabilityDefinition
from opsmesh.identity.users.models import UserProfile
from opsmesh.platform.approvals.models import ApprovalRequest
from opsmesh.shared.audit import AuditActor
from opsmesh.shared.errors import ConflictError
from tests.capabilities.test_capabilities_postgres import (
    build_extension_zip,
    build_skill_zip,
    extension_configuration,
)
from tests.execution.test_session_routes import app_client as app_client
from tests.execution.test_session_routes import register_account

ROOT = "/api/v1/capabilities"
MCP = {"transport": "remote", "endpoint": "https://example.com/mcp", "tools": []}
TOOL = {
    "execution_mode": "sdk_export",
    "entrypoint": "example:exports",
    "operations": [{"name": "analyze"}],
}


@pytest.fixture
def accounts(database_sessions):
    result = {}
    for name in ("owner", "other", "admin"):
        email = f"publication-{name}@example.com"
        token = register_account(database_sessions, email=email)
        result[name] = {"X-Session-ID": str(token)}
    with database_sessions() as session:
        admin = session.scalar(
            select(UserProfile).where(UserProfile.email == "publication-admin@example.com")
        )
        admin.platform_roles = ["admin"]
        session.commit()
    return result


def create(client, accounts, *, resource="mcp", visibility="private", key="review-me"):
    response = client.post(
        f"{ROOT}/definitions",
        headers=accounts["owner"],
        json={
            "key": key,
            "name": key,
            "visibility": visibility,
            "declaration": {
                "type": "tool" if resource == "tools" else resource,
                "configuration": MCP if resource == "mcp" else TOOL,
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish(client, accounts, version):
    response = client.post(
        f"{ROOT}/definitions/{version['definition_id']}/versions/{version['version']}/publish",
        headers=accounts["owner"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def submit(client, accounts, version):
    response = client.post(
        f"{ROOT}/definitions/{version['definition_id']}/versions/"
        f"{version['version']}/publication-review",
        headers=accounts["owner"],
        json={"submission_note": "Please review this release."},
    )
    assert response.status_code == 201, response.text
    return response.json()


def decide(client, accounts, review, decision="approved"):
    return client.post(
        f"{ROOT}/publication-reviews/admin/{review['review_id']}/decision",
        headers=accounts["admin"],
        json={"decision": decision, "decision_note": "Reviewed."},
    )


@pytest.mark.parametrize("resource", ["mcp", "tools"])
def test_public_visibility_requires_review_and_each_version_is_reviewed(
    app_client, accounts, resource
):
    draft = create(app_client, accounts, resource=resource, visibility="public")
    definition = f"{ROOT}/definitions/{draft['definition_id']}"
    assert app_client.get(definition, headers=accounts["other"]).status_code == 404
    published = publish(app_client, accounts, draft)
    assert app_client.get(definition, headers=accounts["other"]).status_code == 404
    review = submit(app_client, accounts, published)
    queue = app_client.get(f"{ROOT}/publication-reviews/admin", headers=accounts["admin"])
    assert queue.status_code == 200, queue.text
    assert queue.json()[0]["version"]["digest"] == published["digest"]
    assert decide(app_client, accounts, review).status_code == 200
    assert decide(app_client, accounts, review).status_code == 200  # Identical retry.
    assert decide(app_client, accounts, review, "rejected").status_code == 409
    assert app_client.get(definition, headers=accounts["other"]).status_code == 200
    update = app_client.put(
        f"{ROOT}/definitions/{draft['definition_id']}/draft",
        headers=accounts["owner"],
        json={
            "declaration": {
                "type": "tool" if resource == "tools" else resource,
                "configuration": MCP if resource == "mcp" else TOOL,
            }
        },
    )
    assert update.status_code == 200, update.text
    draft2 = update.json()
    assert app_client.get(f"{definition}/versions/2", headers=accounts["other"]).status_code == 404
    publish(app_client, accounts, draft2)
    versions = app_client.get(f"{definition}/versions", headers=accounts["other"]).json()
    assert [version["version"] for version in versions] == [1]
    assert app_client.get(f"{definition}/versions/2", headers=accounts["admin"]).status_code == 200
    assert app_client.get(definition, headers=accounts["other"]).json()["latest_version"] == 1
    catalog = app_client.get(
        f"{ROOT}/definitions", headers=accounts["other"], params={"key_query": "review-me"}
    ).json()
    assert catalog["items"][0]["latest_version"] == 1
    binding = app_client.post(
        "/api/v1/orchestration/capability-bindings",
        headers=accounts["other"],
        json={"name": "cannot-bypass", "version_id": draft2["version_id"], "configuration": {}},
    )
    assert binding.status_code == 404, binding.text
    review2 = submit(app_client, accounts, draft2)
    assert decide(app_client, accounts, review2, "rejected").status_code == 200
    assert app_client.get(f"{definition}/versions/2", headers=accounts["other"]).status_code == 404
    assert app_client.get(f"{definition}/versions/1", headers=accounts["other"]).status_code == 200
    review3 = submit(app_client, accounts, draft2)
    assert decide(app_client, accounts, review3).status_code == 200
    allowed = app_client.post(
        "/api/v1/orchestration/capability-bindings",
        headers=accounts["other"],
        json={"name": "reviewed-version", "version_id": draft2["version_id"], "configuration": {}},
    )
    assert allowed.status_code == 201, allowed.text


def test_review_authorization_withdraw_and_visibility_cancellation(app_client, accounts):
    draft = create(app_client, accounts)
    definition = f"{ROOT}/definitions/{draft['definition_id']}"
    url = f"{definition}/versions/1/publication-review"
    assert app_client.post(url, headers=accounts["owner"], json={}).status_code == 409
    publish(app_client, accounts, draft)
    assert app_client.post(url, headers=accounts["other"], json={}).status_code == 403
    visibility = app_client.post(
        f"{definition}/visibility", headers=accounts["owner"], json={"visibility": "public"}
    )
    assert visibility.status_code == 403, visibility.text
    review = submit(app_client, accounts, draft)
    assert app_client.post(url, headers=accounts["owner"], json={}).status_code == 409
    assert (
        app_client.get(f"{ROOT}/publication-reviews/admin", headers=accounts["other"]).status_code
        == 403
    )
    read = f"{ROOT}/publication-reviews/{review['review_id']}"
    assert app_client.get(read, headers=accounts["other"]).status_code == 404
    assert app_client.get(read, headers=accounts["owner"]).status_code == 200
    unauthorized = app_client.post(
        f"{ROOT}/publication-reviews/admin/{review['review_id']}/decision",
        headers=accounts["owner"],
        json={"decision": "approved"},
    )
    assert unauthorized.status_code == 403
    assert app_client.post(f"{read}/withdraw", headers=accounts["other"]).status_code == 403
    assert (
        app_client.post(f"{read}/withdraw", headers=accounts["owner"]).json()["status"]
        == "cancelled"
    )
    assert decide(app_client, accounts, review).status_code == 409
    review = submit(app_client, accounts, draft)
    # Even an already-private definition can withdraw all pending public releases.
    response = app_client.post(
        f"{definition}/visibility", headers=accounts["owner"], json={"visibility": "private"}
    )
    assert response.status_code == 200, response.text
    assert decide(app_client, accounts, review).status_code == 409
    reviews = app_client.get(f"{ROOT}/publication-reviews/mine", headers=accounts["owner"]).json()
    assert {r["status"] for r in reviews} == {"cancelled"}
    mine = app_client.get(f"{ROOT}/definitions?owned_only=true", headers=accounts["other"]).json()
    assert mine["items"] == []
    approved = submit(app_client, accounts, draft)
    assert decide(app_client, accounts, approved).status_code == 200
    app_client.post(
        f"{definition}/visibility", headers=accounts["owner"], json={"visibility": "private"}
    )
    assert decide(app_client, accounts, approved).status_code == 200
    assert app_client.get(definition, headers=accounts["other"]).status_code == 404


def test_private_and_draft_packages_are_not_downloadable(app_client, accounts, database_sessions):
    created = app_client.post(
        f"{ROOT}/definitions",
        headers=accounts["owner"],
        json={
            "key": "workspace-guide",
            "name": "Guide",
            "visibility": "public",
            "declaration": {"type": "skill"},
        },
    )
    assert created.status_code == 201, created.text
    draft = created.json()
    definition = f"{ROOT}/definitions/{draft['definition_id']}"
    payload = build_skill_zip()
    attached = app_client.put(
        f"{ROOT}/definitions/{draft['definition_id']}/draft/package",
        headers={**accounts["owner"], "Content-Type": "application/octet-stream"},
        content=payload,
    )
    assert attached.status_code == 200, attached.text
    download = f"{definition}/versions/1/package"
    assert app_client.get(download, headers=accounts["other"]).status_code == 404
    publish(app_client, accounts, draft)
    assert app_client.get(download, headers=accounts["other"]).status_code == 404
    review = submit(app_client, accounts, draft)
    assert app_client.get(download, headers=accounts["admin"]).content == payload
    assert decide(app_client, accounts, review).status_code == 200
    assert app_client.get(download, headers=accounts["other"]).content == payload
    with database_sessions() as session:
        definition_row = session.get(CapabilityDefinition, UUID(draft["definition_id"]))
        lifecycle.update_draft(
            session,
            definition_id=definition_row.id,
            configuration=None,
            actor=AuditActor(actor_type="user", actor_id=str(definition_row.created_by_user_id)),
            storage_root=Path(".opsmesh-test-storage"),
            package=payload,
            filename="next.zip",
        )
        session.commit()
    assert (
        app_client.get(f"{definition}/versions/2/package", headers=accounts["other"]).status_code
        == 404
    )
    assert (
        app_client.get(f"{definition}/versions/2/package", headers=accounts["owner"]).content
        == payload
    )


def test_concurrent_decisions_have_one_winner(app_client, accounts, database_sessions):
    version = publish(app_client, accounts, create(app_client, accounts))
    review = submit(app_client, accounts, version)
    with database_sessions() as session:
        admin_id = session.scalar(
            select(UserProfile.id).where(UserProfile.email == "publication-admin@example.com")
        )
    barrier = Barrier(2)

    def make_decision(decision):
        barrier.wait(timeout=10)
        with database_sessions() as session:
            try:
                lifecycle.decide_publication_review(
                    session,
                    review_id=UUID(review["review_id"]),
                    actor=AuditActor(actor_type="user", actor_id=str(admin_id)),
                    actor_is_platform_admin=True,
                    decision=decision,
                    decision_note="Concurrent.",
                )
                session.commit()
                return decision
            except ConflictError:
                session.rollback()
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(make_decision, ("approved", "rejected")))
    assert outcomes.count("conflict") == 1
    winner = next(outcome for outcome in outcomes if outcome != "conflict")
    with database_sessions() as session:
        record = session.get(ApprovalRequest, UUID(review["review_id"]))
        definition = session.get(CapabilityDefinition, UUID(version["definition_id"]))
        assert record.status == winner
        assert definition.visibility == ("public" if winner == "approved" else "private")


def test_approval_refuses_private_dependencies_without_partial_publication(
    app_client, accounts, database_sessions, tmp_path
):
    dependency = publish(
        app_client, accounts, create(app_client, accounts, key="private-dependency")
    )
    with database_sessions() as session:
        owner = session.scalar(
            select(UserProfile.id).where(UserProfile.email == "publication-owner@example.com")
        )
        definition, _ = lifecycle.create_definition(
            session,
            capability_type="mcp",
            key="dependent",
            name="Dependent",
            description="",
            visibility="private",
            configuration=MCP,
            dependencies=[{"type": "mcp", "key": "private-dependency", "version": 1}],
            actor=AuditActor(actor_type="user", actor_id=str(owner)),
            storage_root=tmp_path,
        )
        session.commit()
        definition_id = str(definition.id)
    target = {"definition_id": definition_id, "version": 1}
    publish(app_client, accounts, target)
    review = submit(app_client, accounts, target)
    assert decide(app_client, accounts, review).status_code == 409
    read = app_client.get(
        f"{ROOT}/publication-reviews/{review['review_id']}", headers=accounts["admin"]
    )
    assert read.json()["status"] == "pending"
    assert (
        app_client.get(f"{ROOT}/definitions/{definition_id}", headers=accounts["other"]).status_code
        == 404
    )
    assert decide(app_client, accounts, submit(app_client, accounts, dependency)).status_code == 200
    assert decide(app_client, accounts, review).status_code == 200


def test_system_catalog_also_hides_unpublished_versions(
    app_client, accounts, database_sessions, tmp_path
):
    actor = AuditActor(actor_type="platform")
    with database_sessions() as session:
        definition, _ = lifecycle.create_definition(
            session,
            capability_type="mcp",
            key="system-source",
            name="System source",
            description="",
            visibility="public",
            configuration=MCP,
            source="system",
            actor=actor,
            storage_root=tmp_path,
        )
        lifecycle.publish_version(
            session,
            definition_id=definition.id,
            version_number=1,
            actor=actor,
            storage_root=tmp_path,
        )
        lifecycle.update_draft(
            session,
            definition_id=definition.id,
            configuration=MCP,
            actor=actor,
            storage_root=tmp_path,
        )
        session.commit()
    root = f"{ROOT}/definitions/{definition.id}"
    assert [
        v["version"] for v in app_client.get(f"{root}/versions", headers=accounts["other"]).json()
    ] == [1]
    assert app_client.get(f"{root}/versions/2", headers=accounts["other"]).status_code == 404


def test_system_import_requires_admin_and_records_platform_source(app_client, accounts):
    package = build_extension_zip(b"# static package; never imported\n")
    request = {
        "key": "center-example",
        "name": "Center example",
        "source": "system",
        "visibility": "public",
        "declaration": {"type": "extension", "configuration": extension_configuration()},
    }
    url = f"{ROOT}/definitions"
    assert app_client.post(url, headers=accounts["other"], json=request).status_code == 403
    imported = app_client.post(url, headers=accounts["admin"], json=request)
    assert imported.status_code == 201, imported.text
    definition_id = imported.json()["definition_id"]
    attached = app_client.put(
        f"{url}/{definition_id}/draft/package",
        headers={**accounts["admin"], "Content-Type": "application/octet-stream"},
        content=package,
    )
    assert attached.status_code == 200, attached.text
    published = app_client.post(
        f"{url}/{definition_id}/versions/1/publish", headers=accounts["admin"]
    )
    assert published.status_code == 200, published.text
    definition = app_client.get(
        f"{ROOT}/definitions/{definition_id}",
        headers=accounts["other"],
    ).json()
    assert definition["source"] == "system"
    assert definition["created_by_user_id"] is None


def test_sdk_publication_review_calls_the_real_routes(app_client, accounts):
    version = publish(app_client, accounts, create(app_client, accounts))

    async def verify():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app_client.app)) as owner_http,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app_client.app)) as admin_http,
        ):

            def group(name, http):
                token = accounts[name]["X-Session-ID"]
                transport = HttpTransport("http://testserver", session_id=token, client=http)
                client = PlatformClient("http://testserver", session_id=token, transport=transport)
                return create_capabilities_client(client).publication_reviews

            owner = group("owner", owner_http)
            admin = group("admin", admin_http)
            review = await owner.submit(
                UUID(version["definition_id"]),
                1,
                PublicationReviewSubmission(submission_note="SDK submission"),
            )
            queue = await admin.admin_list()
            assert [r.review_id for r in queue] == [review.review_id]
            approved = await admin.decide(
                review.review_id,
                PublicationReviewDecision(decision="approved", decision_note="SDK review"),
            )
            assert approved.status == "approved"
            assert (await owner.read(review.review_id)).reviewed_by_user_id is not None
            assert (await owner.mine())[0].review_id == review.review_id

    asyncio.run(verify())


def test_generic_approval_inbox_preserves_publication_guards_and_replay(app_client, accounts):
    draft = create(app_client, accounts)
    publish(app_client, accounts, draft)
    review = submit(app_client, accounts, draft)
    url = f"/api/v1/platform/approvals/{review['review_id']}/approve"
    decision = {"idempotency_key": "generic-publication", "reason": "Reviewed."}
    assert app_client.post(url, headers=accounts["owner"], json=decision).status_code == 403
    inbox = app_client.get("/api/v1/platform/approvals", headers=accounts["admin"])
    assert inbox.status_code == 200, inbox.text
    assert review["review_id"] in [item["approval_id"] for item in inbox.json()["approvals"]]
    accepted = app_client.post(url, headers=accounts["admin"], json=decision)
    assert accepted.status_code == 200, accepted.text
    definition = f"{ROOT}/definitions/{draft['definition_id']}"
    assert app_client.get(definition, headers=accounts["other"]).status_code == 200
    hidden = app_client.post(
        f"{definition}/visibility", headers=accounts["owner"], json={"visibility": "private"}
    )
    assert hidden.status_code == 200, hidden.text
    assert app_client.post(url, headers=accounts["admin"], json=decision).status_code == 200
    assert app_client.get(definition, headers=accounts["other"]).status_code == 404
    next_review = submit(app_client, accounts, draft)
    url = f"/api/v1/platform/approvals/{next_review['review_id']}/approve"
    app_client.post(
        f"{definition}/visibility", headers=accounts["owner"], json={"visibility": "private"}
    )
    assert app_client.post(url, headers=accounts["admin"], json=decision).status_code == 409
