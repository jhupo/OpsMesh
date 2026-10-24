"""Authorize a pinned plugin and build its invocation context."""

import importlib.metadata
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from agents import trace
from opsmesh_sdk import Decision, InvocationContext, InvocationSource, Suspended
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.service import usable
from opsmesh.catalog.deps import InvocationDependencies
from opsmesh.catalog.service import authorize_pins
from opsmesh.catalog.service import get as get_publication
from opsmesh.gateway.context import InvocationChannel
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.loader import load
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.quotas.config import ModelDefaults
from opsmesh.quotas.storage import check
from opsmesh.resources.runtime import InvocationResources
from opsmesh.sessions.models import AgentSession
from opsmesh.sessions.runtime import invocation_session
from opsmesh.shared.context import LogContext, logging_context
from opsmesh.shared.errors import ConflictError
from opsmesh.users.service import identity

from ..events import plugin_event
from ..interactions import checkpoint, validate_checkpoint
from ..models import TaskAttempt, TaskCheckpoint, TaskInteraction
from .children import ChildTasks
from .leases import TaskLeases


class TaskCheckpoints:
    def __init__(
        self,
        leases: TaskLeases,
        cipher: SecretCipher,
        task_id: str,
        attempt_id: str,
        *,
        resumable: bool,
    ):
        self.leases, self.cipher = leases, cipher
        self.task_id, self.attempt_id = task_id, attempt_id
        self.resumable = resumable

    async def checkpoint(self, value: dict[str, Any]) -> str:
        if not self.resumable:
            raise ConflictError("Saving checkpoints requires a resume handler")
        with self.leases.sessions.begin() as db:
            task = self.leases.active(db, self.task_id, self.attempt_id)
            return checkpoint(db, self.cipher, task, self.attempt_id, value).id


class TaskEvents:
    def __init__(self, leases: TaskLeases, cipher: SecretCipher, task_id: str, attempt_id: str):
        self.leases, self.cipher = leases, cipher
        self.task_id, self.attempt_id = task_id, attempt_id

    async def emit(self, event: str, data: Any) -> int:
        with self.leases.sessions.begin() as db:
            task = self.leases.active(db, self.task_id, self.attempt_id)
            return plugin_event(db, self.cipher, task, event, data)


class TaskInvoker:
    def __init__(
        self,
        sessions: sessionmaker,
        cipher: SecretCipher,
        registry: PluginRegistry,
        root: Path,
        leases: TaskLeases,
        children: ChildTasks,
        *,
        model_defaults: ModelDefaults,
        session_ttl_seconds: int,
        encryption_key: str,
        sandbox_root: str,
    ):
        self.sessions, self.cipher, self.registry, self.root = sessions, cipher, registry, root
        self.leases, self.children = leases, children
        self.model_defaults = model_defaults
        self.session_ttl_seconds, self.encryption_key, self.sandbox_root = (
            session_ttl_seconds,
            encryption_key,
            sandbox_root,
        )

    async def invoke(self, task_id: str, attempt_id: str) -> Any:
        with self.sessions.begin() as db:
            task = self.leases.active(db, task_id, attempt_id)
            user = identity(db, task.owner_id)
            if task.agent_id:
                usable(db, user, task.agent_id)
            if task.snapshot.get("publication_id"):
                get_publication(db, user, task.snapshot["publication_id"])
            authorize_pins(db, user, task.snapshot["dependencies"])
            identity(db, task.snapshot["owner_id"])
            plugin = load(db, self.root, self.registry, task.snapshot["plugin_id"])
            entry = plugin.entries[task.snapshot["entry"]]
            value = self.cipher.decrypt(task.encrypted_input)
            state, decision = None, None
            if task.checkpoint_id:
                saved = db.get(TaskCheckpoint, task.checkpoint_id)
                if (
                    saved is None
                    or saved.task_id != task.id
                    or saved.sdk_version != importlib.metadata.version("openai-agents")
                ):
                    raise ConflictError("Checkpoint SDK version differs from the runtime")
                state = self.cipher.decrypt(saved.encrypted_state)
                validate_checkpoint(db, task, saved)
                if entry.resume is None:
                    raise ConflictError("Plugin has no resume handler")
            if task.decision_id:
                answered = db.get(TaskInteraction, task.decision_id)
                if (
                    answered is not None
                    and answered.status == "answered"
                    and answered.checkpoint_id == task.checkpoint_id
                ):
                    decision = Decision(
                        answered.id,
                        answered.responder_id or "",
                        self.cipher.decrypt(answered.encrypted_decision or ""),
                    )
            source_payload = self.cipher.decrypt(task.encrypted_source)
            source_payload.pop("target", None)
            source = InvocationSource(**source_payload)
            snapshot = task.snapshot
            attempt = db.get(TaskAttempt, attempt_id)
            assert attempt is not None
            trace_id = attempt.trace_id
            conversation = db.get(AgentSession, task.session_id) if task.session_id else None
            if conversation is None:
                raise ConflictError("Task has no session")
        token = logging_context.set(
            LogContext(
                task_id=task_id,
                attempt_id=attempt_id,
                user_id=user.user_id,
                plugin_id=snapshot["plugin_id"],
                agent_id=snapshot["id"],
                session_id=task.session_id,
            )
        )
        try:
            async with AsyncExitStack() as stack:
                stack.enter_context(
                    trace(
                        "OpsMesh task",
                        trace_id=trace_id,
                        group_id=task_id,
                        metadata={"task_id": task_id, "plugin_id": snapshot["plugin_id"]},
                    )
                )
                resources = InvocationResources(
                    self.sessions,
                    self.cipher,
                    self.root,
                    snapshot,
                    user.user_id,
                    task_id,
                    stack,
                    self.model_defaults,
                )
                dependencies = InvocationDependencies(
                    self.sessions,
                    self.cipher,
                    self.registry,
                    self.root,
                    snapshot["dependencies"],
                    user.user_id,
                    lambda publication, data, key: self.children.call(
                        task, attempt_id, publication, data, key
                    ),
                )
                interactions = TaskCheckpoints(
                    self.leases,
                    self.cipher,
                    task_id,
                    attempt_id,
                    resumable=entry.resume is not None,
                )
                context = InvocationContext(
                    user=user,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    config=self.cipher.decrypt(snapshot["encrypted_config"]),
                    resources=resources,
                    dependencies=dependencies,
                    interactions=interactions,
                    events=TaskEvents(self.leases, self.cipher, task_id, attempt_id),
                    session=invocation_session(
                        self.sessions,
                        self.cipher,
                        self.root,
                        conversation,
                        task_id,
                        stack,
                        encryption_key=self.encryption_key,
                        ttl=self.session_ttl_seconds,
                        sandbox_root=self.sandbox_root,
                        validate=lambda db, cleanup=False: self.leases.active(
                            db, task_id, attempt_id, cleanup=cleanup
                        ),
                        plugin_id=snapshot["plugin_id"],
                        config=self.cipher.decrypt(snapshot["encrypted_config"]),
                        backends=frozenset(plugin.sandboxes),
                    ),
                    metadata=value.get("metadata", {}),
                    source=source,
                    checkpoint=state,
                    decision=decision,
                    channel=InvocationChannel(
                        self.sessions,
                        self.cipher,
                        lambda db: self.leases.active(db, task_id, attempt_id),
                    )
                    if source.channel_id
                    else None,
                )
                dependencies.context = context
                handler = entry.resume if state is not None else entry.handler
                assert handler is not None
                result = await handler(context, value["value"])
                if isinstance(result, Suspended) and entry.resume is None:
                    raise ConflictError("Suspending an entry requires a resume handler")
                with self.sessions.begin() as db:
                    check(db, self.root, snapshot["owner_id"], 0)
                return result
        finally:
            logging_context.reset(token)
