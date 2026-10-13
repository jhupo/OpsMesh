"""Connection administration and transactional, connection-scoped ingress."""

from uuid import UUID

from opentelemetry import context, propagate
from opsmesh_plugin_sdk.messaging.contracts import ChannelEvent
from opsmesh_plugin_sdk.packaging.packages import SignedPluginPackage
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.capabilities.plugins.models import PluginBinding
from opsmesh.capabilities.plugins.policy import require_plugin_resource
from opsmesh.capabilities.references.schema import reject_embedded_secrets
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.messaging.channels.models import ChannelConnection, ChannelIngress
from opsmesh.messaging.channels.schemas import (
    ChannelConnectionCreateRequest,
    ChannelConnectionUpdateRequest,
)
from opsmesh.orchestration.automations.contracts import AutomationConfiguration
from opsmesh.orchestration.automations.models import Automation, AutomationEvent
from opsmesh.orchestration.automations.service import AutomationService
from opsmesh.shared.db.errors import flush_or_raise_conflict
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.egress import EgressURLPolicy, validate_egress_url
from opsmesh.shared.security.secrets import SecretEncryptionService
from opsmesh.shared.utils import payload_hash


class ChannelService:
    """Callers own the transaction; ingress commits only after both facts are staged."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def require(self, workspace_id: UUID, connection_id: UUID) -> ChannelConnection:
        connection = self.session.scalar(
            select(ChannelConnection)
            .where(
                ChannelConnection.workspace_id == workspace_id,
                ChannelConnection.id == connection_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if connection is None:
            raise ResourceAccessDeniedError()
        return connection

    def require_principal(
        self, principal: PluginPrincipal, connection_id: UUID, permission: str
    ) -> ChannelConnection:
        PluginAuthenticationService(self.session).require(principal, permission)
        connection = self.require(principal.workspace_id, connection_id)
        if connection.install_id != principal.install_id or connection.status != "active":
            raise ResourceAccessDeniedError()
        require_plugin_resource(
            self.session,
            principal.workspace_id,
            "channel",
            connection.id,
            required_feature="attachments" if permission == "attachments.write" else "receive",
        )
        return connection

    def create(
        self,
        workspace_id: UUID,
        actor_id: UUID,
        request: ChannelConnectionCreateRequest,
        secrets: SecretEncryptionService,
    ) -> ChannelConnection:
        auth = PluginAuthenticationService(self.session)
        installation = auth.active_install(workspace_id, request.install_id, lock=True)
        release = auth.active_release(installation)
        package = SignedPluginPackage.model_validate(release.package)
        capability = next(
            (item for item in package.manifest.capabilities if item.key == request.capability_key),
            None,
        )
        if capability is None or capability.kind != "channel":
            raise ValueError("Installation does not declare this channel capability")
        self._validate_configuration(capability.configuration_schema, request.configuration)
        encrypted = secrets.encrypt_payload(
            {"signing_secret": request.signing_secret.get_secret_value()}
        )
        connection = ChannelConnection(
            workspace_id=workspace_id,
            install_id=installation.id,
            capability_key=capability.key,
            name=request.name,
            account_id=request.account_id,
            endpoint_url=validate_egress_url(
                request.endpoint_url, policy=EgressURLPolicy(allowed_schemes=frozenset({"https"}))
            ),
            configuration=request.configuration,
            configuration_revision=1,
            encrypted_signing_secret=encrypted.ciphertext,
            encryption_key_id=encrypted.key_id,
        )
        self.session.add(connection)
        flush_or_raise_conflict(self.session, "Channel connection name already exists")
        self.session.add(
            PluginBinding(
                workspace_id=workspace_id,
                install_id=installation.id,
                capability_key=capability.key,
                kind="channel",
                resource_id=connection.id,
                configuration=request.configuration,
                configuration_revision=1,
            )
        )
        self._audit(connection, actor_id, "channel.created")
        self.session.flush()
        return connection

    def update(
        self,
        workspace_id: UUID,
        connection_id: UUID,
        actor_id: UUID,
        request: ChannelConnectionUpdateRequest,
        secrets: SecretEncryptionService,
    ) -> ChannelConnection:
        connection = self.require(workspace_id, connection_id)
        if connection.configuration_revision != request.expected_revision:
            raise DomainError(
                "Channel configuration changed", code="channel_revision_conflict", status_code=409
            )
        auth = PluginAuthenticationService(self.session)
        installation = auth.active_install(workspace_id, connection.install_id, lock=True)
        package = SignedPluginPackage.model_validate(auth.active_release(installation).package)
        capability = next(
            (
                item
                for item in package.manifest.capabilities
                if item.key == connection.capability_key
            ),
            None,
        )
        if capability is None or capability.kind != "channel":
            raise ValueError("Current release no longer declares this channel")
        self._validate_configuration(capability.configuration_schema, request.configuration)
        endpoint = validate_egress_url(
            request.endpoint_url, policy=EgressURLPolicy(allowed_schemes=frozenset({"https"}))
        )
        changed = (
            connection.account_id != request.account_id
            or connection.endpoint_url != endpoint
            or connection.configuration != request.configuration
            or connection.status != request.status
            or request.signing_secret is not None
        )
        connection.name = request.name
        connection.account_id = request.account_id
        connection.endpoint_url = endpoint
        connection.configuration = request.configuration
        connection.status = request.status
        if changed:
            connection.configuration_revision += 1
        if request.signing_secret is not None:
            encrypted = secrets.encrypt_payload(
                {"signing_secret": request.signing_secret.get_secret_value()}
            )
            connection.encrypted_signing_secret = encrypted.ciphertext
            connection.encryption_key_id = encrypted.key_id
        binding = self.session.scalar(
            select(PluginBinding).where(
                PluginBinding.workspace_id == workspace_id,
                PluginBinding.install_id == connection.install_id,
                PluginBinding.kind == "channel",
                PluginBinding.resource_id == connection.id,
            )
        )
        if binding is None:
            raise ResourceAccessDeniedError()
        binding.configuration = dict(request.configuration)
        binding.configuration_revision = connection.configuration_revision
        self._audit(connection, actor_id, "channel.updated")
        return connection

    def receive(
        self, principal: PluginPrincipal, connection_id: UUID, envelope: ChannelEvent
    ) -> AutomationEvent:
        connection = self.require_principal(principal, connection_id, "messages.receive")
        if envelope.source != f"opsmesh://channels/{connection.id}":
            raise ResourceAccessDeniedError()
        checksum = payload_hash(envelope.data.model_dump(mode="json"))
        existing = self.session.scalar(
            select(ChannelIngress).where(
                ChannelIngress.workspace_id == principal.workspace_id,
                ChannelIngress.connection_id == connection.id,
                ChannelIngress.source == envelope.source,
                ChannelIngress.external_id == envelope.id,
            )
        )
        if existing is not None:
            if existing.content_hash != checksum:
                raise DomainError(
                    "Event identity reused with different data",
                    code="channel_event_conflict",
                    status_code=409,
                )
            event = self.session.scalar(
                select(AutomationEvent).where(
                    AutomationEvent.workspace_id == principal.workspace_id,
                    AutomationEvent.id == existing.event_id,
                )
            )
            if event is None:
                raise ValueError("Accepted channel event is missing")
            return event
        item = self.route(connection, envelope.data.conversation_id, envelope.data.sender_id)
        carrier = {
            key: value
            for key, value in {
                "traceparent": envelope.traceparent,
                "tracestate": envelope.tracestate,
            }.items()
            if value is not None
        }
        token = context.attach(propagate.extract(carrier)) if carrier else None
        try:
            event = AutomationService(self.session).receive(
                principal.workspace_id,
                item.id,
                principal,
                envelope.data,
                commit=False,
            )
        finally:
            if token is not None:
                context.detach(token)
        self.session.add(
            ChannelIngress(
                workspace_id=principal.workspace_id,
                connection_id=connection.id,
                source=envelope.source,
                external_id=envelope.id,
                content_hash=checksum,
                automation_id=item.id,
                event_id=event.id,
            )
        )
        self.session.flush()
        return event

    def route(
        self, connection: ChannelConnection, conversation_id: str, sender_id: str
    ) -> Automation:
        matches = []
        for automation in self.session.scalars(
            select(Automation).where(
                Automation.workspace_id == connection.workspace_id,
                Automation.status == "active",
            )
        ):
            config = AutomationConfiguration.model_validate(automation.configuration)
            if (
                config.trigger_type == "message"
                and config.channel_connection_id == connection.id
                and sender_id in config.allowed_senders
                and (
                    not config.allowed_conversations
                    or conversation_id in config.allowed_conversations
                )
            ):
                matches.append(automation)
        if len(matches) != 1:
            raise DomainError(
                "Channel message must match exactly one Automation",
                code="channel_route_ambiguous" if matches else "channel_route_missing",
                status_code=409,
            )
        return matches[0]

    @staticmethod
    def _validate_configuration(
        schema: dict[str, object], configuration: dict[str, object]
    ) -> None:
        from jsonschema import Draft202012Validator

        reject_embedded_secrets(configuration)
        if not Draft202012Validator(schema).is_valid(configuration):
            raise ValueError("Channel configuration does not satisfy the capability schema")

    def _audit(self, connection: ChannelConnection, actor_id: UUID, action: str) -> None:
        AuditService(self.session).record_user_action(
            workspace_id=connection.workspace_id,
            user_id=actor_id,
            action=action,
            target_type="channel_connection",
            target_id=connection.id,
            metadata={
                "install_id": str(connection.install_id),
                "configuration_revision": connection.configuration_revision,
            },
        )
