"""Durable admission, scheduling and reply routing for configured business workflows."""

from uuid import UUID, uuid5

from opsmesh_plugin_sdk.messaging.contracts import (
    AcceptedEvent,
    AutomationReply,
    EventState,
    IncomingMessage,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.capabilities.plugins.authentication import PluginAuthenticationService, PluginPrincipal
from opsmesh.capabilities.plugins.policy import (
    plugin_resource_available,
    require_plugin_resource,
)
from opsmesh.capabilities.references.schema import reject_embedded_secrets
from opsmesh.governance.audit.service import AuditService
from opsmesh.identity.authorization.execution import ExecutionIdentityService
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.messaging.channels.models import ChannelConnection, ChannelDelivery
from opsmesh.orchestration.automations.authorization import require_automation_principal
from opsmesh.orchestration.automations.contracts import (
    AutomationConfiguration,
    AutomationUpdateRequest,
)
from opsmesh.orchestration.automations.conversations import AutomationConversationService
from opsmesh.orchestration.automations.execution_contracts import ReplyPreparation
from opsmesh.orchestration.automations.identities import AutomationSenderIdentityService
from opsmesh.orchestration.automations.io import (
    external_output,
    model_message,
    validate_follow_up,
)
from opsmesh.orchestration.automations.models import Automation, AutomationEvent
from opsmesh.orchestration.automations.plugin_attachments import PluginAttachmentService
from opsmesh.orchestration.definitions.service import OrchestrationDefinitionService
from opsmesh.orchestration.runs.models import AgentRun
from opsmesh.orchestration.scheduling.contracts import ScheduleOccurrence
from opsmesh.orchestration.scheduling.service import stage_schedule_control
from opsmesh.orchestration.tasks.models import Task
from opsmesh.orchestration.tasks.service import TaskCreateCommand, WorkspaceTaskService
from opsmesh.orchestration.tasks.state import TERMINAL_TASK_STATUSES
from opsmesh.platform.updates.admission import require_execution_admission
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.runtime.temporal.outbox import stage_execution
from opsmesh.shared.errors import DomainError
from opsmesh.shared.security.redaction import redact_sensitive_payload
from opsmesh.shared.utils import payload_hash
from opsmesh.teams.management.models import AgentTeam
from opsmesh.workspaces.projects.models import WorkspaceProject


class AutomationService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_automations(
        self,
        workspace_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Automation]:
        return list(
            self._session.scalars(
                select(Automation)
                .where(Automation.workspace_id == workspace_id)
                .order_by(Automation.created_at.desc(), Automation.id)
                .limit(limit)
                .offset(offset)
            )
        )

    def require(self, workspace_id: UUID, automation_id: UUID) -> Automation:
        item = self._session.scalar(
            select(Automation)
            .where(
                Automation.workspace_id == workspace_id,
                Automation.id == automation_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if item is None:
            raise ValueError("Automation not found")
        return item

    def create(
        self,
        workspace_id: UUID,
        user_id: UUID,
        config: AutomationConfiguration,
    ) -> Automation:
        self._validate_targets(workspace_id, config)
        item = Automation(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            configuration=config.model_dump(mode="json"),
            execution_identity=ExecutionIdentityService(self._session).capture(
                workspace_id,
                user_id,
            ),
            next_due_at=None,
        )
        self._session.add(item)
        self._session.flush()
        self._stage_schedule(item, user_id)
        self._audit(item, "automation.created", user_id)
        self._session.commit()
        return item

    def update(
        self,
        workspace_id: UUID,
        automation_id: UUID,
        user_id: UUID,
        request: AutomationUpdateRequest,
    ) -> Automation:
        item = self.require(workspace_id, automation_id)
        if item.version != request.expected_version:
            raise ValueError("Automation version changed; reload before editing")
        previous = AutomationConfiguration.model_validate(item.configuration)
        contract_fields = {"input_schema", "output_schema", "model_input_fields", "output_binding"}
        if (
            previous.model_dump(include=contract_fields)
            != request.configuration.model_dump(include=contract_fields)
            and request.configuration.contract_version <= previous.contract_version
        ):
            raise ValueError("Changing message contracts requires a higher contract_version")
        if request.configuration.contract_version < previous.contract_version:
            raise ValueError("Message contract versions cannot move backwards")
        self._validate_targets(workspace_id, request.configuration)
        item.configuration = request.configuration.model_dump(mode="json")
        item.status = request.status
        item.version += 1
        item.next_due_at = None
        self._stage_schedule(item, user_id)
        self._audit(item, "automation.updated", user_id)
        self._session.commit()
        return item

    def receive(
        self,
        workspace_id: UUID,
        automation_id: UUID,
        user_id: UUID | PluginPrincipal,
        message: IncomingMessage,
        *,
        commit: bool = True,
    ) -> AutomationEvent:
        item = self.require(workspace_id, automation_id)
        config = AutomationConfiguration.model_validate(item.configuration)
        if config.channel_connection_id is not None:
            require_plugin_resource(
                self._session, workspace_id, "channel", config.channel_connection_id
            )
        reject_embedded_secrets(message.data)
        reject_embedded_secrets(message.text)
        if item.status != "active" or config.trigger_type != "message":
            raise ValueError("Message automation is not active")
        source_install_id = None
        if isinstance(user_id, PluginPrincipal):
            if user_id.workspace_id != workspace_id:
                raise ResourceAccessDeniedError()
            PluginAuthenticationService(self._session).require_automation(
                user_id, item.id, "messages.receive"
            )
            source_install_id = user_id.install_id
        elif user_id != item.created_by_user_id:
            raise ValueError("Message ingress requires the automation's configured principal")
        require_automation_principal(self._session, item)
        if message.sender_id not in config.allowed_senders:
            raise ValueError("Sender is not authorized for this automation")
        if message.action not in config.allowed_message_actions:
            raise ValueError("Message action is not enabled for this automation")
        model_message(config, message)
        identity = AutomationSenderIdentityService(self._session).resolve(item, message.sender_id)
        PluginAttachmentService(self._session).require_references(
            item,
            message,
            ExecutionIdentityService(self._session).restore(workspace_id, identity),
            source_install_id,
        )
        if message.action == "follow_up":
            validate_follow_up(config, message)
        target = AutomationConversationService(self._session).target(item, message)
        if target is not None and target.source_install_id != source_install_id:
            raise ResourceAccessDeniedError()
        event = self._accept(
            item,
            external_id=message.event_id,
            conversation_id=message.conversation_id,
            payload=message.model_dump(mode="json"),
            execution_identity=identity,
            source_install_id=source_install_id,
            source_connection_id=config.channel_connection_id,
        )
        if commit:
            self._session.commit()
        return event

    def event_state(self, workspace_id: UUID, automation_id: UUID, event_id: UUID) -> EventState:
        conversations = AutomationConversationService(self._session)
        event = conversations.require_event(workspace_id, automation_id, event_id)
        from opsmesh.orchestration.automations.authorization import require_event_principal

        require_event_principal(self._session, self.require(workspace_id, automation_id), event)
        task = conversations.task(event)
        output = external_output(self._session, event, task)
        return EventState(
            event=AcceptedEvent.model_validate(event),
            task_status=task.status if task else None,
            output=output.value,
            output_error=output.error,
            contract_version=AutomationConfiguration.model_validate(
                event.configuration
            ).contract_version,
            pending_actions=conversations.pending_actions(event),
            notification_sequence=event.notification_sequence,
        )

    def events(
        self,
        workspace_id: UUID,
        automation_id: UUID,
        *,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AutomationEvent]:
        item = self.require(workspace_id, automation_id)
        query = select(AutomationEvent).where(
            AutomationEvent.workspace_id == workspace_id,
            AutomationEvent.automation_id == automation_id,
        )
        if item.created_by_user_id != user_id:
            query = query.where(
                AutomationEvent.execution_identity["user_id"].as_string() == str(user_id)
            )
        return list(
            self._session.scalars(
                query.order_by(AutomationEvent.created_at.desc(), AutomationEvent.id)
                .limit(limit)
                .offset(offset)
            )
        )

    def _accept(
        self,
        item: Automation,
        *,
        external_id: str,
        conversation_id: str,
        payload: dict[str, object],
        execution_identity: dict[str, object] | None,
        source_install_id: UUID | None = None,
        source_connection_id: UUID | None = None,
    ) -> AutomationEvent:
        checksum = payload_hash(payload)
        principal = ExecutionIdentityService(self._session).restore(
            item.workspace_id, execution_identity
        )
        existing = self._session.scalar(
            select(AutomationEvent).where(
                AutomationEvent.workspace_id == item.workspace_id,
                AutomationEvent.automation_id == item.id,
                AutomationEvent.external_event_id == external_id,
                AutomationEvent.source_connection_id == source_connection_id,
            )
        )
        if existing is not None:
            if existing.source_install_id != source_install_id:
                raise ResourceAccessDeniedError()
            if existing.content_hash != checksum:
                raise ValueError("Event ID was already used for different content")
            if existing.execution_identity != execution_identity:
                raise ValueError("Event ID belongs to a different accepted identity")
            return existing
        require_execution_admission(self._session)
        event = AutomationEvent(
            workspace_id=item.workspace_id,
            automation_id=item.id,
            external_event_id=external_id,
            conversation_id=conversation_id,
            content_hash=checksum,
            configuration=dict(item.configuration),
            input_payload=payload,
            execution_identity=execution_identity,
            source_install_id=source_install_id,
            source_connection_id=source_connection_id,
        )
        self._session.add(event)
        self._session.flush()
        AuditService(self._session).record_user_action(
            workspace_id=item.workspace_id,
            user_id=principal.user_id,
            action="automation.event_accepted",
            target_type="automation_event",
            target_id=event.id,
            metadata={
                "automation_id": str(item.id),
                "source_install_id": str(source_install_id) if source_install_id else None,
            },
        )
        stage_execution(
            self._session,
            ExecutionRequest(
                execution_id=event.id,
                workspace_id=event.workspace_id,
                resource_id=event.id,
                kind=ExecutionKind.ENTRY_EVENT,
                requested_by_user_id=principal.user_id,
            ),
        )
        return event

    def dispatch_event(self, workspace_id: UUID, event_id: UUID) -> str:
        event = self._require_event(workspace_id, event_id)
        if event.status != "pending":
            return event.status
        try:
            with self._session.begin_nested():
                self._admit_event(event)
        except (ValueError, DomainError):
            event.status = "rejected"
            event.error_code = "automation_admission_rejected"
            AuditService(self._session).record_system_action(
                workspace_id=event.workspace_id,
                action="automation.event_rejected",
                target_type="automation_event",
                target_id=event.id,
                metadata={"error_code": event.error_code},
            )
        if event.status in {"dispatched", "control_applied"}:
            stage_execution(
                self._session,
                ExecutionRequest(
                    execution_id=event.id,
                    workspace_id=event.workspace_id,
                    resource_id=event.id,
                    kind=ExecutionKind.REPLY_DELIVERY,
                ),
                dispatch_id=uuid5(event.id, "reply_delivery"),
            )
            if event.task_id is not None:
                from opsmesh.orchestration.automations.dispatch import (
                    stage_automation_task_progress,
                )

                stage_automation_task_progress(
                    self._session,
                    workspace_id=event.workspace_id,
                    task_id=event.task_id,
                    event_id=uuid5(event.id, "admitted"),
                )
        self._session.flush()
        return event.status

    def accept_scheduled_occurrence(self, item: Automation, occurrence: ScheduleOccurrence) -> None:
        self._accept(
            item,
            external_id=f"schedule:{uuid5(item.id, occurrence.occurrence_id)}",
            conversation_id=f"schedule:{item.id}",
            payload={"scheduled_at": occurrence.started_at.isoformat()},
            execution_identity=item.execution_identity,
        )
        item.next_due_at = None

    def _admit_event(self, event: AutomationEvent) -> None:
        item = self.require(event.workspace_id, event.automation_id)
        live_configuration = AutomationConfiguration.model_validate(item.configuration)
        if event.source_connection_id is not None:
            connection = self._session.scalar(
                select(ChannelConnection)
                .where(
                    ChannelConnection.workspace_id == event.workspace_id,
                    ChannelConnection.id == event.source_connection_id,
                    ChannelConnection.status == "active",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if (
                connection is None
                or live_configuration.channel_connection_id != connection.id
                or (
                    event.source_install_id is not None
                    and connection.install_id != event.source_install_id
                )
            ):
                raise ValueError("Accepted channel connection is disabled or changed")
        if item.status != "active" or (
            live_configuration.channel_connection_id is not None
            and not plugin_resource_available(
                self._session,
                event.workspace_id,
                "channel",
                live_configuration.channel_connection_id,
                required_feature="receive",
            )
        ):
            raise ValueError("Automation admission is unavailable")
        require_automation_principal(self._session, item)
        config = AutomationConfiguration.model_validate(event.configuration)
        principal = ExecutionIdentityService(self._session).restore(
            event.workspace_id, event.execution_identity
        )
        message = None
        if config.trigger_type == "message":
            message = IncomingMessage.model_validate(event.input_payload)
            if (
                AutomationSenderIdentityService(self._session).resolve(item, message.sender_id)
                != event.execution_identity
            ):
                raise ValueError("Message identity binding was changed")
            PluginAttachmentService(self._session).require_references(
                item, message, principal, event.source_install_id
            )
            live = AutomationConfiguration.model_validate(item.configuration)
            if (
                message.sender_id not in live.allowed_senders
                or message.action not in live.allowed_message_actions
                or (
                    live.allowed_conversations
                    and message.conversation_id not in live.allowed_conversations
                )
            ):
                raise ValueError("Message permission was revoked")
            if AutomationConversationService(self._session).dispatch(item, event, message).handled:
                return
        active_task = self._session.scalar(
            select(Task.id)
            .join(AutomationEvent, AutomationEvent.task_id == Task.id)
            .where(
                AutomationEvent.workspace_id == event.workspace_id,
                AutomationEvent.automation_id == item.id,
                AutomationEvent.source_connection_id.is_not_distinct_from(
                    event.source_connection_id
                ),
                AutomationEvent.conversation_id == event.conversation_id,
                Task.workspace_id == event.workspace_id,
                Task.status.not_in([status.value for status in TERMINAL_TASK_STATUSES])
                | select(AgentRun.id)
                .where(
                    AgentRun.workspace_id == event.workspace_id,
                    AgentRun.task_id == Task.id,
                    AgentRun.started_at.is_not(None),
                    AgentRun.execution_released_at.is_(None),
                )
                .exists(),
            )
            .limit(1)
        )
        if active_task is not None:
            if config.overlap_policy == "skip" and (
                message is None or message.action != "follow_up"
            ):
                event.status = "skipped"
            return
        self._validate_targets(event.workspace_id, config)
        task = WorkspaceTaskService(self._session).create_automation_task(
            workspace_id=event.workspace_id,
            user_id=principal.user_id,
            execution_identity=event.execution_identity or {},
            command=TaskCreateCommand(
                title=config.name,
                agent_team_id=config.agent_team_id,
                workspace_project_id=config.workspace_project_id,
                orchestration_definition_id=config.orchestration_definition_id,
                orchestration_version=config.orchestration_version,
                input={
                    **config.input_defaults,
                    "event": model_message(config, message)
                    if message is not None
                    else event.input_payload,
                },
                generic_state={"automation_event_id": str(event.id)},
            ),
        )
        event.task_id = task.id
        event.status = "dispatched"

    def prepare_reply(self, workspace_id: UUID, event_id: UUID) -> ReplyPreparation:
        event = self._require_event(workspace_id, event_id)
        if event.reply_delivery_id is not None:
            return ReplyPreparation(complete=False, delivery_id=event.reply_delivery_id)
        if event.status not in {"dispatched", "control_applied"}:
            return ReplyPreparation(complete=event.status != "pending")
        task = AutomationConversationService(self._session).task(event)
        if task is None:
            raise ValueError("Automation reply has no task")
        item = self.require(workspace_id, event.automation_id)
        try:
            from opsmesh.orchestration.automations.authorization import require_event_principal

            require_event_principal(self._session, item, event)
            if item.status != "active":
                raise ValueError("Automation is inactive")
            config = AutomationConfiguration.model_validate(event.configuration)
            live = AutomationConfiguration.model_validate(item.configuration)
            if (
                config.trigger_type == "message"
                and event.input_payload.get("sender_id") not in live.allowed_senders
            ):
                raise ValueError("Sender permission revoked")
        except (ValueError, DomainError):
            event.status = "rejected"
            event.error_code = "automation_reply_authorization_revoked"
            return ReplyPreparation(complete=True)
        terminal = task.status in {status.value for status in TERMINAL_TASK_STATUSES}
        complete = terminal or event.status == "control_applied"
        if config.reply_connection_id is None:
            if complete:
                event.status = "completed"
            return ReplyPreparation(complete=complete)
        if not complete and not config.notify_progress:
            return ReplyPreparation(complete=False)
        actions = AutomationConversationService(self._session).pending_actions(event)
        output = external_output(self._session, event, task)
        payload = AutomationReply(
            contract_version=config.contract_version,
            error_code=output.error,
            sequence=event.notification_sequence + 1,
            automation_id=item.id,
            event_id=event.id,
            conversation_id=event.conversation_id,
            source_event_id=event.external_event_id,
            sender_id=str(event.input_payload["sender_id"])
            if "sender_id" in event.input_payload
            else None,
            task_id=task.id,
            status="output_rejected" if output.error else task.status,
            kind="control_applied"
            if event.status == "control_applied"
            else "result"
            if terminal
            else "action_required"
            if actions
            else "progress",
            output=output.value,
            pending_actions=actions,
        ).model_dump(mode="json")
        fingerprint = payload_hash(
            {key: value for key, value in payload.items() if key != "sequence"}
        )
        if not complete and fingerprint == event.progress_fingerprint:
            return ReplyPreparation(complete=False)
        connection = self._session.scalar(
            select(ChannelConnection).where(
                ChannelConnection.workspace_id == workspace_id,
                ChannelConnection.id == config.reply_connection_id,
                ChannelConnection.status == "active",
            )
        )
        if connection is None or live.reply_connection_id != connection.id:
            event.status = "rejected"
            event.error_code = "reply_connection_unavailable"
            return ReplyPreparation(complete=True)
        require_plugin_resource(self._session, workspace_id, "channel", connection.id)
        delivery = ChannelDelivery(
            workspace_id=workspace_id,
            connection_id=connection.id,
            event_id=event.id,
            sequence=event.notification_sequence + 1,
            connection_revision=connection.configuration_revision,
            payload=redact_sensitive_payload(payload),
        )
        self._session.add(delivery)
        self._session.flush()
        event.notification_sequence += 1
        event.reply_delivery_id = delivery.id
        event.reply_final = complete
        event.progress_fingerprint = fingerprint
        if complete:
            event.status = "reply_pending"
        self._session.flush()
        return ReplyPreparation(complete=False, delivery_id=delivery.id)

    def settle_reply(self, workspace_id: UUID, event_id: UUID) -> bool:
        event = self._require_event(workspace_id, event_id)
        if event.reply_delivery_id is None:
            return event.status in {"completed", "rejected", "reply_failed"}
        delivery = self._session.scalar(
            select(ChannelDelivery).where(
                ChannelDelivery.workspace_id == workspace_id,
                ChannelDelivery.id == event.reply_delivery_id,
            )
        )
        if delivery is None or delivery.status not in {"delivered", "failed", "unknown"}:
            raise ValueError("Reply delivery is not terminal")
        if delivery.status in {"failed", "unknown"}:
            event.status = "reply_failed"
            event.error_code = "reply_delivery_failed"
            return True
        if event.reply_final:
            event.status = "completed"
            event.error_code = None
            return True
        event.reply_delivery_id = None
        return False

    def _require_event(self, workspace_id: UUID, event_id: UUID) -> AutomationEvent:
        event = self._session.scalar(
            select(AutomationEvent)
            .where(AutomationEvent.workspace_id == workspace_id, AutomationEvent.id == event_id)
            .with_for_update()
        )
        if event is None:
            raise ValueError("Automation event not found")
        return event

    def _stage_schedule(self, item: Automation, user_id: UUID) -> None:
        # Message definitions also dispatch control so changing a scheduled trigger
        # to a message trigger removes the previously created native schedule.
        stage_schedule_control(
            self._session,
            workspace_id=item.workspace_id,
            resource_id=item.id,
            owner_kind="automation",
            user_id=user_id,
        )

    def _validate_targets(self, workspace_id: UUID, config: AutomationConfiguration) -> None:
        definitions = OrchestrationDefinitionService(self._session)
        definition = definitions.get_definition(workspace_id, config.orchestration_definition_id)
        revision = definitions.get_revision(
            workspace_id, config.orchestration_definition_id, config.orchestration_version
        )
        if definition is None or definition.status == "archived" or revision is None:
            raise ValueError("Automation requires a retained published workflow revision")
        nodes = revision.definition.get("nodes", [])
        agent_nodes = (
            {
                node.get("package_id")
                for node in nodes
                if isinstance(node, dict) and node.get("node_type", "agent") == "agent"
            }
            if isinstance(nodes, list)
            else set()
        )
        if not set(config.stream_output_nodes) <= agent_nodes:
            raise ValueError("Stream output nodes must name agents in the published workflow")
        if (
            self._session.scalar(
                select(AgentTeam.id).where(
                    AgentTeam.workspace_id == workspace_id,
                    AgentTeam.id == config.agent_team_id,
                    AgentTeam.status == "active",
                )
            )
            is None
        ):
            raise ValueError("Active team not found")
        if (
            config.workspace_project_id is not None
            and self._session.scalar(
                select(WorkspaceProject.id).where(
                    WorkspaceProject.workspace_id == workspace_id,
                    WorkspaceProject.id == config.workspace_project_id,
                    WorkspaceProject.status == "active",
                )
            )
            is None
        ):
            raise ValueError("Active project not found")
        for connection_id, feature in (
            (config.channel_connection_id, "receive"),
            (config.reply_connection_id, "send"),
        ):
            if connection_id is None:
                continue
            connection = self._session.scalar(
                select(ChannelConnection).where(
                    ChannelConnection.workspace_id == workspace_id,
                    ChannelConnection.id == connection_id,
                    ChannelConnection.status == "active",
                )
            )
            if connection is None:
                raise ValueError("Channel connection is unavailable")
            require_plugin_resource(
                self._session, workspace_id, "channel", connection.id, required_feature=feature
            )

    def _audit(self, item: Automation, action: str, user_id: UUID) -> None:
        AuditService(self._session).record_user_action(
            workspace_id=item.workspace_id,
            user_id=user_id,
            action=action,
            target_type="automation",
            target_id=item.id,
            metadata={"version": item.version},
        )
