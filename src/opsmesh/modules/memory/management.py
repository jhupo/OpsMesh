"""Owner-controlled memory policy changes with durable operation auditing."""

from collections.abc import Awaitable, Callable

from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation

from .api import (
    MemoryCandidate,
    MemoryJob,
    MemoryNamespace,
    MemoryNamespaceSettings,
    MemoryRetentionResult,
    MemoryService,
    MemorySource,
)


class MemoryManagement:
    def __init__(
        self,
        memory: MemoryService,
        audit: AuditJournal,
        *,
        validate_actor: Callable[[str], Awaitable[None]],
    ) -> None:
        self._memory, self._audit = memory, audit
        self._validate_actor = validate_actor

    async def create_namespace(
        self, namespace: MemoryNamespace, *, actor_id: str
    ) -> MemoryNamespace:
        await self._validate_actor(actor_id)
        if namespace.owner != actor_id:
            raise OpsMeshError("AUTHORIZATION_DENIED", "Namespace owner must be the creating actor")
        async with audited_operation(self._audit, "memory.create", namespace.id, actor_id):
            return await self._memory.create_namespace(namespace)

    async def list_namespaces(self, *, actor_id: str) -> tuple[MemoryNamespace, ...]:
        await self._validate_actor(actor_id)
        return await self._memory.list_namespaces(principal_id=actor_id)

    async def configure_namespace(
        self,
        namespace_id: str,
        settings: MemoryNamespaceSettings,
        *,
        actor_id: str,
        expected_revision: int,
    ) -> MemoryNamespace:
        await self._validate_actor(actor_id)
        async with audited_operation(self._audit, "memory.configure", namespace_id, actor_id):
            return await self._memory.configure_namespace(
                namespace_id,
                settings,
                principal_id=actor_id,
                expected_revision=expected_revision,
            )

    async def get_namespace(self, namespace_id: str, *, principal_id: str) -> MemoryNamespace:
        await self._validate_actor(principal_id)
        return await self._memory.get_namespace(namespace_id, principal_id=principal_id)

    async def read(self, namespace_id: str, *, principal_id: str) -> MemoryCandidate:
        await self._validate_actor(principal_id)
        return await self._memory.read(namespace_id, principal_id=principal_id)

    async def append_source(self, source: MemorySource, *, principal_id: str) -> MemorySource:
        await self._validate_actor(principal_id)
        async with audited_operation(
            self._audit, "memory.append_source", source.namespace_id, principal_id
        ):
            return await self._memory.append_source(source, principal_id=principal_id)

    async def update(
        self,
        namespace_id: str,
        candidate: MemoryCandidate,
        *,
        principal_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> MemoryNamespace:
        await self._validate_actor(principal_id)
        async with audited_operation(self._audit, "memory.update", namespace_id, principal_id):
            return await self._memory.update(
                namespace_id,
                candidate,
                principal_id=principal_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )

    async def forget(self, namespace_id: str, *, principal_id: str) -> None:
        await self._validate_actor(principal_id)
        async with audited_operation(self._audit, "memory.forget", namespace_id, principal_id):
            await self._memory.forget(namespace_id, principal_id=principal_id)

    async def prune(self, namespace_id: str, *, principal_id: str) -> MemoryRetentionResult:
        await self._validate_actor(principal_id)
        async with audited_operation(self._audit, "memory.prune", namespace_id, principal_id):
            return await self._memory.prune(namespace_id, principal_id=principal_id)

    async def request_generation(
        self, namespace_id: str, *, principal_id: str, idempotency_key: str
    ) -> MemoryJob:
        await self._validate_actor(principal_id)
        async with audited_operation(self._audit, "memory.generate", namespace_id, principal_id):
            return await self._memory.request_generation(
                namespace_id, principal_id=principal_id, idempotency_key=idempotency_key
            )

    async def get_job(self, job_id: str, *, principal_id: str) -> MemoryJob:
        await self._validate_actor(principal_id)
        return await self._memory.get_job(job_id, principal_id=principal_id)

    async def list_jobs(self, namespace_id: str, *, principal_id: str) -> tuple[MemoryJob, ...]:
        await self._validate_actor(principal_id)
        return await self._memory.list_jobs(namespace_id, principal_id=principal_id)
