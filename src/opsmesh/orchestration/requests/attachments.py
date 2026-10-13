"""Materialize authorized message inputs at the model execution boundary only."""
from contextlib import closing

from opsmesh_plugin_sdk.messaging.contracts import IncomingMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from opsmesh.agents.execution.contracts import AgentInputAttachment
from opsmesh.identity.authorization.resources import ResourceAccessDeniedError
from opsmesh.orchestration.automations.authorization import (
    require_automation_principal,
    require_event_principal,
)
from opsmesh.orchestration.automations.models import Automation, AutomationEvent
from opsmesh.orchestration.automations.plugin_attachments import PluginAttachmentService
from opsmesh.orchestration.tasks.models import Task
from opsmesh.resources.files.content import WorkspaceFileContentReader
from opsmesh.resources.files.models import WorkspaceFile
from opsmesh.resources.storage.backends import create_storage
from opsmesh.shared.config import Settings


def message_attachments(
    session: Session,
    task: Task | None,
    settings: Settings,
) -> tuple[AgentInputAttachment, ...]:
    if task is None:
        return ()
    event = session.scalar(
        select(AutomationEvent).where(
            AutomationEvent.workspace_id == task.workspace_id,
            AutomationEvent.task_id == task.id,
            AutomationEvent.input_payload["action"].as_string() == "start",
        )
    )
    if event is None or not event.input_payload.get("attachments"):
        return ()
    message = IncomingMessage.model_validate(event.input_payload)
    item = session.scalar(
        select(Automation).where(
            Automation.workspace_id == task.workspace_id,
            Automation.id == event.automation_id,
            Automation.status == "active",
        )
    )
    if item is None:
        raise ResourceAccessDeniedError()
    require_automation_principal(session, item)
    actor = require_event_principal(session, item, event)
    PluginAttachmentService(session).require_references(
        item, message, actor, event.source_install_id
    )
    if sum(item.size_bytes for item in message.attachments) > 20 * 1024 * 1024:
        raise ValueError("Combined model attachments exceed 20 MiB")
    result = []
    with closing(create_storage(settings)) as storage:
        for attachment in message.attachments:
            file = session.scalar(
                select(WorkspaceFile).where(
                    WorkspaceFile.workspace_id == task.workspace_id,
                    WorkspaceFile.id == attachment.file_id,
                )
            )
            if file is None:
                raise ResourceAccessDeniedError()
            content = WorkspaceFileContentReader(
                storage,
                max_bytes=20 * 1024 * 1024,
                allowed_content_types=frozenset({attachment.content_type}),
            ).read_bytes(file, workspace_id=task.workspace_id)
            result.append(
                AgentInputAttachment(
                    file_id=file.id,
                    kind=attachment.kind,
                    filename=file.filename,
                    content_type=file.content_type,
                    content=content,
                    transcript=attachment.transcript,
                )
            )
    return tuple(result)
