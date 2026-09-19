"""Materialize authorized message inputs at the model execution boundary only."""

from opsmesh_plugin_sdk.contracts import IncomingMessage
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.config import Settings
from backend.app.domains.access.resources import ResourceAccessDenied
from backend.app.domains.agents.runtime.contracts import AgentInputAttachment
from backend.app.domains.integrations.automation_authorization import (
    require_automation_principal,
    require_event_principal,
)
from backend.app.domains.integrations.automation_models import Automation, AutomationEvent
from backend.app.domains.integrations.plugin_attachments import PluginAttachmentService
from backend.app.domains.orchestration.tasks.models import Task
from backend.app.domains.workspace.storage.content import WorkspaceFileContentReader
from backend.app.domains.workspace.storage.models import WorkspaceFile
from backend.app.domains.workspace.storage.storage import create_storage


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
        raise ResourceAccessDenied()
    require_automation_principal(session, item)
    actor = require_event_principal(session, item, event)
    PluginAttachmentService(session).require_references(
        item, message, actor, event.source_install_id
    )
    if sum(item.size_bytes for item in message.attachments) > 20 * 1024 * 1024:
        raise ValueError("Combined model attachments exceed 20 MiB")
    storage = create_storage(settings)
    result = []
    for attachment in message.attachments:
        file = session.scalar(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == task.workspace_id,
                WorkspaceFile.id == attachment.file_id,
            )
        )
        if file is None:
            raise ResourceAccessDenied()
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
