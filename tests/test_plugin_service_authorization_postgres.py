"""Service identity survives safe upgrades and never bypasses live workspace grants."""

import base64
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
from opsmesh_plugin_sdk.packaging.packages import sign_package
from opsmesh_plugin_sdk.services.observability import PluginLog
from opsmesh_plugin_sdk.services.storage import StoreWrite
from sqlalchemy import func, select

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService
from opsmesh.capabilities.plugins.configuration import PluginConfigurationService
from opsmesh.capabilities.plugins.contracts import PluginInstallRequest, PluginTrustKeyCreateRequest
from opsmesh.capabilities.plugins.lifecycle import PluginLifecycleService
from opsmesh.capabilities.plugins.logging import record_plugin_log
from opsmesh.capabilities.plugins.models import PluginCredential
from opsmesh.governance.audit.models import AuditEvent
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from tests.fixtures.database import product_session
from tests.fixtures.temporal import seed_workspace


def test_service_token_upgrade_permission_intersection_tenant_scope_and_log_pipeline(caplog):
    session = product_session()
    owner, workspace = seed_workspace(session)
    _, foreign_workspace = seed_workspace(session)
    lifecycle = PluginLifecycleService(session)
    private_key = Ed25519PrivateKey.generate()
    lifecycle.trust_key(
        workspace.id,
        owner.id,
        PluginTrustKeyCreateRequest(
            key_id="publisher",
            plugin_key="org.opsmesh.services",
            public_key=base64.b64encode(private_key.public_key().public_bytes_raw()).decode(),
        ),
    )
    permissions = ["storage.read", "storage.write", "logs.write"]

    def release(version, grants):
        return sign_package(
            PluginManifest.model_validate(
                {
                    "key": "org.opsmesh.services",
                    "version": version,
                    "name": "Services",
                    "capabilities": [
                        {
                            "key": "tools",
                            "kind": "mcp_server",
                            "title": "Tools",
                            "required_permissions": grants,
                        }
                    ],
                }
            ),
            "publisher",
            private_key.private_bytes_raw(),
        )

    installation = lifecycle.install(
        workspace.id,
        owner.id,
        PluginInstallRequest(
            package=release("1.0.0", permissions),
            instance_name="production",
            approved_permissions=permissions,
        ),
    )
    raw_token = "omp_test_connector_service_identity"
    credential = PluginCredential(
        workspace_id=workspace.id,
        install_id=installation.id,
        generation=installation.generation,
        token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
        permissions=permissions,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(credential)
    session.commit()
    authentication = PluginAuthenticationService(session)
    principal = authentication.authenticate(workspace.id, installation.id, raw_token)
    store = PluginConfigurationService(session)
    saved = store.write(
        principal, "checkpoint", StoreWrite(expected_revision=0, value={"cursor": 3})
    )
    assert saved.revision == 1
    session.commit()
    generation = installation.generation
    lifecycle.install(
        workspace.id,
        owner.id,
        PluginInstallRequest(
            package=release("1.1.0", permissions),
            instance_name="production",
            installation_id=installation.id,
            expected_generation=generation,
            approved_permissions=permissions,
        ),
    )
    assert installation.generation > credential.generation
    assert authentication.authenticate(workspace.id, installation.id, raw_token) == principal
    assert store.read(principal, "checkpoint").value == {"cursor": 3}
    with pytest.raises(ResourceAccessDeniedError):
        authentication.authenticate(foreign_workspace.id, installation.id, raw_token)
    audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
    with caplog.at_level("INFO", logger="opsmesh.plugins"):
        record_plugin_log(
            session,
            principal,
            PluginLog(code="connector.connected", metadata={"api_key": "sensitive", "count": 1}),
        )
    assert "connector.connected" in caplog.text
    assert "sensitive" not in str(caplog.records[-1].plugin_fields)
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == audit_count
    lifecycle.install(
        workspace.id,
        owner.id,
        PluginInstallRequest(
            package=release("2.0.0", ["storage.read"]),
            instance_name="production",
            installation_id=installation.id,
            expected_generation=installation.generation,
            approved_permissions=["storage.read"],
        ),
    )
    assert authentication.context(principal).permissions == ["storage.read"]
    with pytest.raises(ResourceAccessDeniedError):
        store.write(principal, "checkpoint", StoreWrite(expected_revision=1, value={"cursor": 4}))
    credential.status = "revoked"
    session.commit()
    with pytest.raises(ResourceAccessDeniedError):
        authentication.authenticate(workspace.id, installation.id, raw_token)
