"""Owner-scoped provider credentials; plaintext is accepted only when writing or executing."""

import json
import re
from uuid import uuid4

from pydantic import Field, SecretStr, computed_field

from opsmesh.framework.errors import OpsMeshError
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.infrastructure.database import AsyncDatabase, StoredRecord, VersionedRecordStore
from opsmesh.infrastructure.secrets import FileCredentialCipher
from opsmesh.modules.accounts.api import AccountDirectory
from opsmesh.modules.audit.api import AuditJournal
from opsmesh.modules.audit.operations import audited_operation


class ModelCredential(OpsMeshContract):
    id: str
    owner_id: str
    name: str = Field(min_length=1, max_length=100)
    revision: int
    version: int
    enabled: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ref(self) -> str:
        return f"credential:{self.id}@{self.version}"


class ModelCredentialManagement:
    def __init__(
        self,
        database: AsyncDatabase,
        accounts: AccountDirectory,
        audit: AuditJournal,
        cipher: FileCredentialCipher,
    ) -> None:
        self.store = VersionedRecordStore(database, "models_credentials")
        self.accounts, self.audit, self.cipher = accounts, audit, cipher

    async def _actor(self, actor_id: str) -> None:
        actor = await self.accounts.get_principal(actor_id)
        if not actor.active:
            raise OpsMeshError("FORBIDDEN", "User is disabled")

    @staticmethod
    def _view(row: StoredRecord) -> ModelCredential:
        return ModelCredential(
            id=row.id,
            owner_id=row.data["owner_id"],
            name=row.data["name"],
            revision=row.revision,
            version=row.data["version"],
            enabled=row.data["enabled"],
        )

    async def _owned(self, identifier: str, actor_id: str) -> StoredRecord:
        await self._actor(actor_id)
        row = await self.store.get(identifier)
        if row is None or row.data["owner_id"] != actor_id:
            raise OpsMeshError("NOT_FOUND", "Personal credential does not exist")
        return row

    def _encrypt(self, identifier: str, owner_id: str, revision: int, secret: SecretStr) -> str:
        value = secret.get_secret_value()
        if not value.strip() or len(value) > 16384:
            raise OpsMeshError("INVALID_REQUEST", "Credential must be nonempty and at most 16 KiB")
        return self.cipher.encrypt(json.dumps([identifier, owner_id, revision, value]).encode())

    async def create(self, name: str, secret: SecretStr, *, actor_id: str) -> ModelCredential:
        await self._actor(actor_id)
        identifier = uuid4().hex
        # Validate metadata before persisting a secret.
        ModelCredential(id=identifier, owner_id=actor_id, name=name, revision=1, version=1)
        encrypted = self._encrypt(identifier, actor_id, 1, secret)
        async with audited_operation(self.audit, "model.credential.create", identifier, actor_id):
            row = await self.store.create(
                identifier,
                {
                    "owner_id": actor_id,
                    "name": name,
                    "enabled": True,
                    "version": 1,
                    "versions": {"1": encrypted},
                },
            )
        return self._view(row)

    async def list(self, *, actor_id: str) -> list[ModelCredential]:
        await self._actor(actor_id)
        return [
            self._view(row) for row in await self.store.list() if row.data["owner_id"] == actor_id
        ]

    async def rotate(
        self, identifier: str, secret: SecretStr, expected_revision: int, *, actor_id: str
    ) -> ModelCredential:
        row = await self._owned(identifier, actor_id)
        encrypted = self._encrypt(identifier, actor_id, expected_revision + 1, secret)
        async with audited_operation(self.audit, "model.credential.rotate", identifier, actor_id):
            updated = await self.store.update(
                identifier,
                {
                    **row.data,
                    "version": expected_revision + 1,
                    "versions": {**row.data["versions"], str(expected_revision + 1): encrypted},
                },
                expected_revision,
            )
        return self._view(updated)

    async def set_enabled(
        self, identifier: str, enabled: bool, expected_revision: int, *, actor_id: str
    ) -> ModelCredential:
        row = await self._owned(identifier, actor_id)
        async with audited_operation(
            self.audit, "model.credential.availability", identifier, actor_id
        ):
            result = await self.store.update(
                identifier, {**row.data, "enabled": enabled}, expected_revision
            )
        return self._view(result)

    async def validate_reference(
        self, reference: str, *, actor_id: str
    ) -> tuple[StoredRecord, int]:
        match = re.fullmatch(r"credential:([0-9a-f]{32})@([1-9][0-9]*)", reference)
        if match is None:
            raise OpsMeshError(
                "CONFIG_INVALID", "Personal models require a pinned personal credential"
            )
        row = await self._owned(match[1], actor_id)
        if not row.data["enabled"]:
            raise OpsMeshError("RESOURCE_DISABLED", "Personal credential is disabled")
        version = int(match[2])
        if str(version) not in row.data["versions"]:
            raise OpsMeshError("NOT_FOUND", "Personal credential revision does not exist")
        return row, version

    async def resolve(self, reference: str, *, actor_id: str) -> str:
        row, version = await self.validate_reference(reference, actor_id=actor_id)
        try:
            decoded = json.loads(self.cipher.decrypt(row.data["versions"][str(version)]))
        except (ValueError, UnicodeError):
            raise OpsMeshError(
                "CREDENTIAL_INVALID", "Stored credential payload is invalid"
            ) from None
        if (
            not isinstance(decoded, list)
            or len(decoded) != 4
            or decoded[:3] != [row.id, actor_id, version]
            or not isinstance(decoded[3], str)
        ):
            raise OpsMeshError("CREDENTIAL_INVALID", "Stored credential identity differs")
        return decoded[3]
