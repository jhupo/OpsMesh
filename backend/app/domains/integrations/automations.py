"""Durable admission, scheduling and reply routing for configured business workflows."""

from datetime import UTC, datetime
from uuid import UUID

from opsmesh_plugin_sdk.contracts import AcceptedEvent, AutomationReply, EventState, IncomingMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import DomainError
from backend.app.core.security.redaction import redact_sensitive_payload
from backend.app.core.utils import payload_hash
from backend.app.domains.capabilities.plugins.policy import (
    plugin_resource_available,
    require_plugin_resource,
)
from backend.app.domains.capabilities.resources.schema import reject_embedded_secrets
from backend.app.domains.integrations.automation_authorization import require_automation_principal
from backend.app.domains.integrations.automation_contracts import (
    AutomationConfiguration,
    AutomationUpdate,
)
from backend.app.domains.integrations.automation_conversations import (
    AutomationConversationService,
)
from backend.app.domains.integrations.automation_io import (
    external_output,
    message_instruction,
    model_message,
)
from backend.app.domains.integrations.automation_models import Automation, AutomationEvent
from backend.app.domains.integrations.webhooks.delivery import WebhookDeliveryService
from backend.app.domains.integrations.webhooks.models import (
    WebhookDeliveryAttempt,
    WebhookSubscription,
)
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.orchestration.tasks.service import TaskCreateCommand, WorkspaceTaskService
from backend.app.domains.orchestration.tasks.state import TERMINAL_TASK_STATUSES
from backend.app.domains.orchestration.workflows.definitions.service import (
    OrchestrationDefinitionService,
)
from backend.app.domains.workspace.projects.models import WorkspaceProject
from backend.app.domains.workspace.teams.models import AgentTeam
from backend.app.observability.audit.service import AuditService
from backend.app.runtime.workers.scheduling.calendar import next_run_at, utc_datetime


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
            next_due_at=self._next_due(config),
        )
        self._session.add(item)
        self._session.flush()
        self._audit(item, "automation.created", user_id)
        self._session.commit()
        return item

    def update(
        self,
        workspace_id: UUID,
        automation_id: UUID,
        user_id: UUID,
        request: AutomationUpdate,
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
        item.next_due_at = (
            self._next_due(request.configuration) if item.status == "active" else None
        )
        self._audit(item, "automation.updated", user_id)
        self._session.commit()
        return item

    def receive(
        self,
        workspace_id: UUID,
        automation_id: UUID,
        user_id: UUID,
        message: IncomingMessage,
    ) -> AutomationEvent:
        item = self.require(workspace_id, automation_id)
        config = AutomationConfiguration.model_validate(item.configuration)
        require_plugin_resource(self._session, workspace_id, "message_trigger", item.id)
        reject_embedded_secrets(message.data)
        reject_embedded_secrets(message.text)
        if item.status != "active" or config.trigger_type != "message":
            raise ValueError("Message automation is not active")
        if user_id != item.created_by_user_id:
            raise ValueError("Message ingress requires the automation's configured principal")
        require_automation_principal(self._session, item)
        if message.sender_id not in config.allowed_senders:
            raise ValueError("Sender is not authorized for this automation")
        if message.action not in config.allowed_message_actions:
            raise ValueError("Message action is not enabled for this automation")
        model_message(config, message)
        if message.action in {"follow_up", "add_instruction"}:
            message_instruction(config, message)
        AutomationConversationService(self._session).target(item, message)
        event = self._accept(
            item,
            external_id=message.event_id,
            conversation_id=message.conversation_id,
            payload=message.model_dump(mode="json"),
        )
        self._session.commit()
        return event

    def event_state(self, workspace_id: UUID, automation_id: UUID, event_id: UUID) -> EventState:
        conversations = AutomationConversationService(self._session)
        event = conversations.require_event(workspace_id, automation_id, event_id)
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
        limit: int = 50,
        offset: int = 0,
    ) -> list[AutomationEvent]:
        self.require(workspace_id, automation_id)
        return list(
            self._session.scalars(
                select(AutomationEvent)
                .where(
                    AutomationEvent.workspace_id == workspace_id,
                    AutomationEvent.automation_id == automation_id,
                )
                .order_by(AutomationEvent.created_at.desc(), AutomationEvent.id)
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
    ) -> AutomationEvent:
        checksum = payload_hash(payload)
        existing = self._session.scalar(
            select(AutomationEvent).where(
                AutomationEvent.workspace_id == item.workspace_id,
                AutomationEvent.automation_id == item.id,
                AutomationEvent.external_event_id == external_id,
            )
        )
        if existing is not None:
            if existing.content_hash != checksum:
                raise ValueError("Event ID was already used for different content")
            return existing
        event = AutomationEvent(
            workspace_id=item.workspace_id,
            automation_id=item.id,
            external_event_id=external_id,
            conversation_id=conversation_id,
            content_hash=checksum,
            configuration=dict(item.configuration),
            input_payload=payload,
        )
        self._session.add(event)
        self._session.flush()
        self._audit(item, "automation.event_accepted", item.created_by_user_id)
        return event

    def maintain(self, *, limit: int = 100) -> None:
        """Bounded DB work; execution and network delivery use existing workers."""
        self._materialize_due(limit)
        self._dispatch_pending(limit)
        self._prepare_replies(limit)
        self._settle_replies(limit)

    def _settle_replies(self, limit: int) -> None:
        pairs = self._session.execute(
            select(AutomationEvent, WebhookDeliveryAttempt)
            .join(
                WebhookDeliveryAttempt,
                AutomationEvent.reply_delivery_id == WebhookDeliveryAttempt.id,
            )
            .where(
                AutomationEvent.status.in_(["reply_pending", "reply_failed"]),
                WebhookDeliveryAttempt.workspace_id == AutomationEvent.workspace_id,
                WebhookDeliveryAttempt.status.in_(["succeeded", "dead_lettered"]),
                (AutomationEvent.status == "reply_pending")
                | (WebhookDeliveryAttempt.status == "succeeded"),
            )
            .order_by(AutomationEvent.updated_at)
            .limit(limit)
            .with_for_update(skip_locked=True, of=AutomationEvent)
        ).all()
        for event, delivery in pairs:
            event.status = "completed" if delivery.status == "succeeded" else "reply_failed"
            event.error_code = None if delivery.status == "succeeded" else "reply_delivery_failed"
        self._session.commit()

    def _materialize_due(self, limit: int) -> None:
        now = datetime.now(UTC)
        items = self._session.scalars(
            select(Automation)
            .where(
                Automation.status == "active",
                Automation.next_due_at <= now,
            )
            .order_by(Automation.next_due_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for item in items:
            config = AutomationConfiguration.model_validate(item.configuration)
            due = utc_datetime(item.next_due_at)
            self._accept(
                item,
                external_id=f"schedule:{due.isoformat()}",
                conversation_id=f"schedule:{item.id}",
                payload={"scheduled_at": due.isoformat()},
            )
            item.next_due_at = (
                None
                if config.schedule_type == "one_shot"
                else next_run_at(
                    schedule_type=str(config.schedule_type),
                    schedule_config=config.schedule_config,
                    after=now,
                    include_now=False,
                )
            )
        self._session.commit()

    def _dispatch_pending(self, limit: int) -> None:
        events = self._session.scalars(
            select(AutomationEvent)
            .where(
                AutomationEvent.status == "pending",
            )
            .order_by(
                AutomationEvent.checked_at.asc().nullsfirst(),
                AutomationEvent.created_at,
                AutomationEvent.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for event in events:
            event.checked_at = datetime.now(UTC)
            try:
                with self._session.begin_nested():
                    item = self.require(event.workspace_id, event.automation_id)
                    if item.status != "active":
                        continue
                    if not plugin_resource_available(
                        self._session,
                        event.workspace_id,
                        "message_trigger",
                        item.id,
                    ):
                        continue
                    require_automation_principal(self._session, item)
                    config = AutomationConfiguration.model_validate(event.configuration)
                    previous_context: dict[str, object] = {}
                    if config.trigger_type == "message":
                        message = IncomingMessage.model_validate(event.input_payload)
                        live_config = AutomationConfiguration.model_validate(item.configuration)
                        if (
                            message.sender_id not in live_config.allowed_senders
                            or message.action not in live_config.allowed_message_actions
                        ):
                            raise ValueError("Message permission was revoked")
                        routed = AutomationConversationService(self._session).dispatch(
                            item, event, message
                        )
                        if routed.handled:
                            continue
                        previous_context = routed.previous_context
                    active_task = self._session.scalar(
                        select(Task.id)
                        .join(AutomationEvent, AutomationEvent.task_id == Task.id)
                        .where(
                            AutomationEvent.workspace_id == event.workspace_id,
                            AutomationEvent.automation_id == item.id,
                            AutomationEvent.conversation_id == event.conversation_id,
                            Task.workspace_id == event.workspace_id,
                            Task.status.not_in([status.value for status in TERMINAL_TASK_STATUSES]),
                        )
                        .limit(1)
                    )
                    if active_task is not None:
                        if config.overlap_policy == "skip":
                            event.status = "skipped"
                        continue
                    self._validate_targets(event.workspace_id, config)
                    task = WorkspaceTaskService(self._session).create_automation_task(
                        workspace_id=event.workspace_id,
                        user_id=item.created_by_user_id,
                        command=TaskCreateCommand(
                            title=config.name,
                            agent_team_id=config.agent_team_id,
                            workspace_project_id=config.workspace_project_id,
                            orchestration_definition_id=config.orchestration_definition_id,
                            orchestration_version=config.orchestration_version,
                            input={
                                **config.input_defaults,
                                "event": model_message(config, message)
                                if config.trigger_type == "message"
                                else event.input_payload,
                                "previous_context": previous_context,
                            },
                            generic_state={"automation_event_id": str(event.id)},
                        ),
                    )
                    event.task_id = task.id
                    event.status = "dispatched"
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
        self._session.commit()

    def _prepare_replies(self, limit: int) -> None:
        pairs = self._session.execute(
            select(AutomationEvent, Task)
            .join(Task, Task.id == AutomationEvent.task_id)
            .where(
                AutomationEvent.status.in_(["dispatched", "control_applied"]),
                Task.workspace_id == AutomationEvent.workspace_id,
            )
            .order_by(AutomationEvent.checked_at.asc().nullsfirst(), AutomationEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True, of=AutomationEvent)
        ).all()
        for event, task in pairs:
            event.checked_at = datetime.now(UTC)
            item = self.require(event.workspace_id, event.automation_id)
            if item.status != "active":
                continue
            try:
                require_automation_principal(self._session, item)
            except ValueError:
                event.status = "rejected"
                event.error_code = "automation_principal_revoked"
                continue
            config = AutomationConfiguration.model_validate(event.configuration)
            live_config = AutomationConfiguration.model_validate(item.configuration)
            if (
                config.trigger_type == "message"
                and event.input_payload.get("sender_id") not in live_config.allowed_senders
            ):
                event.status = "rejected"
                event.error_code = "automation_sender_revoked"
                continue
            terminal = task.status in {status.value for status in TERMINAL_TASK_STATUSES}
            complete = terminal or event.status == "control_applied"
            if config.reply_subscription_id is None:
                if complete:
                    event.status = "completed"
                continue
            if not complete and not config.notify_progress:
                continue
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
                kind=(
                    "control_applied"
                    if event.status == "control_applied"
                    else "result"
                    if terminal
                    else "action_required"
                    if actions
                    else "progress"
                ),
                output=output.value,
                pending_actions=actions,
            ).model_dump(mode="json")
            fingerprint = payload_hash(
                {key: value for key, value in payload.items() if key != "sequence"}
            )
            if not complete and fingerprint == event.progress_fingerprint:
                continue
            attempts = WebhookDeliveryService(self._session).enqueue_event(
                workspace_id=event.workspace_id,
                event_type="automation.reply",
                event_id=f"{event.id}:{event.notification_sequence + 1}",
                subscription_id=config.reply_subscription_id,
                payload=redact_sensitive_payload(payload),
            )
            if not attempts:
                event.status = "rejected"
                event.error_code = "reply_subscription_unavailable"
            elif complete:
                event.notification_sequence += 1
                event.reply_delivery_id = attempts[0].id
                event.status = "reply_pending"
            else:
                event.notification_sequence += 1
                event.progress_fingerprint = fingerprint
        self._session.commit()

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
        if config.reply_subscription_id is not None:
            subscription = self._session.scalar(
                select(WebhookSubscription).where(
                    WebhookSubscription.workspace_id == workspace_id,
                    WebhookSubscription.id == config.reply_subscription_id,
                    WebhookSubscription.status == "active",
                )
            )
            if subscription is None or not {"*", "automation.reply"}.intersection(
                subscription.event_types
            ):
                raise ValueError("Reply subscription must accept automation.reply")

    @staticmethod
    def _next_due(config: AutomationConfiguration) -> datetime | None:
        if config.trigger_type != "schedule":
            return None
        return next_run_at(
            schedule_type=str(config.schedule_type),
            schedule_config=config.schedule_config,
            after=datetime.now(UTC),
            include_now=True,
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
