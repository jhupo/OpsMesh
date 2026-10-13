"""Signed installation and connection fixtures for the channel product flow."""

import base64
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
from opsmesh_plugin_sdk.packaging.packages import sign_package

from opsmesh.capabilities.plugins.authentication import PluginPrincipal
from opsmesh.capabilities.plugins.contracts import PluginInstallRequest, PluginTrustKeyCreateRequest
from opsmesh.capabilities.plugins.lifecycle import PluginLifecycleService
from opsmesh.capabilities.plugins.models import PluginCredential
from opsmesh.messaging.channels.schemas import ChannelConnectionCreateRequest
from opsmesh.messaging.channels.service import ChannelService
from opsmesh.shared.security.secrets import SecretEncryptionService


def seed_channel(session, owner, workspace, *, secret_service=None):
    private = Ed25519PrivateKey.generate()
    lifecycle = PluginLifecycleService(session)
    lifecycle.trust_key(
        workspace.id,
        owner.id,
        PluginTrustKeyCreateRequest(
            key_id="channel-publisher",
            plugin_key="org.opsmesh.channel",
            public_key=base64.b64encode(private.public_key().public_bytes_raw()).decode(),
        ),
    )
    permissions = ["messages.receive", "messages.read", "attachments.write"]
    package = sign_package(
        PluginManifest.model_validate(
            {
                "key": "org.opsmesh.channel",
                "version": "1.0.0",
                "name": "Test channel",
                "capabilities": [
                    {
                        "key": "messaging",
                        "kind": "channel",
                        "title": "Messaging",
                        "features": ["receive", "send", "attachments"],
                        "required_permissions": permissions,
                    }
                ],
            }
        ),
        "channel-publisher",
        private.private_bytes_raw(),
    )
    installation = lifecycle.install(
        workspace.id,
        owner.id,
        PluginInstallRequest(
            package=package,
            instance_name="test",
            approved_permissions=permissions,
        ),
    )
    connection = ChannelService(session).create(
        workspace.id,
        owner.id,
        ChannelConnectionCreateRequest(
            install_id=installation.id,
            capability_key="messaging",
            name="Test",
            account_id="robot",
            endpoint_url="https://example.com/deliveries",
            signing_secret="channel-test-signing-secret-is-long-enough",
        ),
        secret_service or SecretEncryptionService(secret="channel-test-key", key_id="channel-test"),
    )
    token = "omp_" + secrets.token_urlsafe(32)
    credential = PluginCredential(
        workspace_id=workspace.id,
        install_id=installation.id,
        generation=installation.generation,
        token_hash=sha256(token.encode()).hexdigest(),
        permissions=permissions,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session.add(credential)
    session.commit()
    return connection, PluginPrincipal(workspace.id, installation.id, credential.id), token
