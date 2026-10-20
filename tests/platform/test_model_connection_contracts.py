"""Model publication never exposes administrator keys or grants implicit access."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from opsmesh.identity.context import CurrentUser
from opsmesh.orchestration.execution.contracts import ModelConnection
from opsmesh.platform.model_connections import service
from opsmesh.platform.model_connections.schemas import (
    ModelConnectionCreateRequest,
    ModelConnectionView,
)
from opsmesh.shared.errors import ForbiddenError, InvalidArgumentError


def test_model_request_masks_key_and_rejects_user_credential_reference():
    request = ModelConnectionCreateRequest(
        name="platform", api_key="test-model-key", default_model="test-model", models=["test-model"]
    )
    assert "test-model-key" not in repr(request)
    assert "test-model-key" not in request.model_dump_json()
    assert (
        not {"api_key", "encrypted_key", "key_id", "credential_id"}
        & ModelConnectionView.model_fields.keys()
    )
    with pytest.raises(ValidationError):
        ModelConnectionCreateRequest.model_validate(
            {
                **request.model_dump(),
                "credential_id": str(uuid4()),
            }
        )


def test_non_admin_cannot_create_model_upstream_before_database_access():
    actor = CurrentUser(uuid4(), "member@example.test", "Member", "active", frozenset({"member"}))
    with pytest.raises(ForbiddenError):
        service.create_connection(
            None,
            actor=actor,
            cipher=None,
            name="unauthorized",
            api_key="key",
            base_url="https://upstream.example/v1",
            default_model="m",
            models=["m"],
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.test/v1",
        "https://example.test/v1?api_key=secret",
        "file:///tmp/models",
        "https://example.test/#secret",
        "https://example.test:bad/v1",
    ],
)
def test_provider_url_cannot_embed_credentials_or_non_http_transport(url):
    with pytest.raises(InvalidArgumentError):
        service._base_url(url)


def test_admin_upstream_can_use_local_test_server_without_runtime_network_policy():
    assert service._base_url("http://127.0.0.1:8080/v1/") == "http://127.0.0.1:8080/v1"


def test_activity_local_key_is_not_in_connection_representation():
    assert "test-activity-key" not in repr(
        ModelConnection("test-activity-key", "https://example.test", 100)
    )
