"""Installation upgrades preserve ownership and atomically validate live configuration."""

import base64
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from opsmesh_plugin_sdk.packaging.manifest import PluginManifest
from opsmesh_plugin_sdk.packaging.packages import sign_package
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from opsmesh.bootstrap.models import register_models
from opsmesh.capabilities.mcp.models import MCPServer
from opsmesh.capabilities.plugins.contracts import (
    PluginBindingReauthorizeRequest,
    PluginBindingRequest,
    PluginInstallRequest,
    PluginTrustKeyCreateRequest,
)
from opsmesh.capabilities.plugins.lifecycle import PluginLifecycleService
from opsmesh.capabilities.plugins.models import PluginBinding, PluginInstallation, PluginRelease
from opsmesh.capabilities.plugins.policy import plugin_resource_available
from opsmesh.shared.errors import ConflictError, NotFoundError, PolicyDeniedError
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.temporal import seed_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="Real PostgreSQL required"
)


def test_plugin_multi_instance_stable_upgrade_revision_and_revocation():
    with temporary_postgres_schema() as engine:
        register_models().create_all(engine)
        sessions = sessionmaker(engine, expire_on_commit=False)
        with sessions() as session:
            owner, workspace = seed_workspace(session)
            other_owner, other_workspace = seed_workspace(session)
            lifecycle = PluginLifecycleService(session)
            private = Ed25519PrivateKey.generate()
            key = lifecycle.trust_key(
                workspace.id,
                owner.id,
                PluginTrustKeyCreateRequest(
                    key_id="publisher",
                    plugin_key="org.opsmesh.test",
                    public_key=base64.b64encode(private.public_key().public_bytes_raw()).decode(),
                ),
            )

            def package(version, permissions=(), schema=None):
                return sign_package(
                    PluginManifest.model_validate(
                        {
                            "key": "org.opsmesh.test",
                            "version": version,
                            "name": "Test",
                            "capabilities": [
                                {
                                    "key": "tools",
                                    "kind": "mcp_server",
                                    "title": "Tools",
                                    "required_permissions": list(permissions),
                                    "configuration_schema": schema or {},
                                }
                            ],
                        }
                    ),
                    "publisher",
                    private.private_bytes_raw(),
                )

            servers = [
                MCPServer(
                    workspace_id=workspace.id,
                    name=name,
                    server_type="streamable_http",
                    connection={"url": "https://example.test/mcp"},
                )
                for name in ("one", "two")
            ]
            session.add_all(servers)
            session.commit()
            installation = lifecycle.install(
                workspace.id,
                owner.id,
                PluginInstallRequest(
                    package=package("1.0.0"),
                    instance_name="production",
                ),
            )
            another = lifecycle.install(
                workspace.id,
                owner.id,
                PluginInstallRequest(
                    package=package("1.0.0"),
                    instance_name="testing",
                ),
            )
            assert installation.id != another.id
            assert session.scalar(select(func.count()).select_from(PluginRelease)) == 1
            bindings = [
                lifecycle.bind(
                    workspace.id,
                    owner.id,
                    installation.id,
                    PluginBindingRequest(
                        capability_key="tools",
                        resource_id=server.id,
                        expected_generation=installation.generation,
                    ),
                )
                for server in servers
            ]
            ids = [binding.id for binding in bindings]
            generation = installation.generation
            with pytest.raises(PolicyDeniedError):
                lifecycle.install(
                    workspace.id,
                    owner.id,
                    PluginInstallRequest(
                        package=package("2.0.0", ["resources.read"]),
                        instance_name="production",
                        installation_id=installation.id,
                        expected_generation=generation,
                    ),
                )
            session.rollback()
            assert installation.current_version == "1.0.0"
            assert installation.generation == generation
            lifecycle.install(
                workspace.id,
                owner.id,
                PluginInstallRequest(
                    package=package("2.0.0", ["resources.read"]),
                    instance_name="production",
                    installation_id=installation.id,
                    expected_generation=generation,
                    approved_permissions=["resources.read"],
                ),
            )
            assert [
                item.id
                for item in session.scalars(
                    select(PluginBinding).order_by(PluginBinding.created_at)
                )
            ] == ids
            assert another.current_version == "1.0.0"
            generation = installation.generation
            with pytest.raises(ValueError):
                lifecycle.install(
                    workspace.id,
                    owner.id,
                    PluginInstallRequest(
                        package=package(
                            "3.0.0",
                            ["resources.read"],
                            {
                                "type": "object",
                                "required": ["new_required_configuration"],
                            },
                        ),
                        instance_name="production",
                        installation_id=installation.id,
                        expected_generation=generation,
                        approved_permissions=["resources.read"],
                    ),
                )
            session.rollback()
            assert installation.current_version == "2.0.0"
            assert installation.generation == generation
            assert session.scalar(select(func.count()).select_from(PluginRelease)) == 2
            assert plugin_resource_available(session, workspace.id, "mcp_server", servers[0].id)
            servers[0].name = "Renamed display label"
            session.commit()
            assert plugin_resource_available(session, workspace.id, "mcp_server", servers[0].id)
            servers[0].configuration_version += 1
            session.commit()
            assert not plugin_resource_available(session, workspace.id, "mcp_server", servers[0].id)
            with pytest.raises(ConflictError):
                lifecycle.reauthorize(
                    workspace.id,
                    owner.id,
                    installation.id,
                    bindings[0].id,
                    PluginBindingReauthorizeRequest(
                        expected_generation=installation.generation,
                        expected_configuration_revision=1,
                    ),
                )
            session.rollback()
            lifecycle.reauthorize(
                workspace.id,
                owner.id,
                installation.id,
                bindings[0].id,
                PluginBindingReauthorizeRequest(
                    expected_generation=installation.generation, expected_configuration_revision=2
                ),
            )
            assert plugin_resource_available(session, workspace.id, "mcp_server", servers[0].id)
            with pytest.raises(NotFoundError):
                lifecycle.require(other_workspace.id, installation.id)
            session.rollback()
            lifecycle.revoke_key(workspace.id, owner.id, key.id)
            assert not plugin_resource_available(session, workspace.id, "mcp_server", servers[0].id)
            assert session.scalar(select(func.count()).select_from(PluginInstallation)) == 2
