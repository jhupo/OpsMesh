"""Assemble the capability and resource boundaries the feature modules inject.

The storage ledger is the one boundary with a real owner today: identity
owns the quota accounts, and this adapter delegates to its functions
inside the caller's session without committing. The scope admission and
the artifact record authorization stay on their rejecting defaults until
the workspace and execution steps register the real owners.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from opsmesh.capabilities import lifecycle, schemas, validation
from opsmesh.capabilities.mcp import validation as mcp
from opsmesh.capabilities.skills import validation as skills
from opsmesh.capabilities.tools import validation as tools
from opsmesh.identity.quotas import service as quotas
from opsmesh.resources.files.access import StorageQuotaLedger


def assemble_capability_validation() -> None:
    lifecycle.configure_validation(
        {
            "mcp": lifecycle.CapabilityValidation(
                configuration=mcp.validate_configuration,
                package=mcp.intake_package,
                version=mcp.validate_version,
            ),
            "skill": lifecycle.CapabilityValidation(
                configuration=skills.validate_configuration,
                package=skills.intake_package,
                version=skills.validate_version,
            ),
            "tool": lifecycle.CapabilityValidation(
                configuration=tools.validate_configuration,
                package=tools.intake_package,
                version=tools.validate_version,
            ),
            "channel": lifecycle.CapabilityValidation(
                configuration=schemas.validated_channel_configuration,
                package=validation.channel_intake_package,
                version=validation.validate_channel_version,
            ),
            "extension": lifecycle.CapabilityValidation(
                configuration=schemas.validated_extension_configuration,
                package=validation.extension_intake_package,
                version=validation.validate_extension_version,
            ),
        }
    )


class IdentityStorageQuotaLedger:
    """The identity-owned storage ledger, called inside the caller's session."""

    def reserve_storage(
        self,
        session: Session,
        *,
        user_id: UUID,
        amount: int,
        idempotency_scope: str,
        idempotency_key: str,
    ) -> UUID:
        reservation = quotas.reserve_storage(
            session,
            user_id=user_id,
            amount=amount,
            idempotency_scope=idempotency_scope,
            idempotency_key=idempotency_key,
        )
        return reservation.id

    def settle_storage(self, session: Session, *, reservation_id: UUID, actual_amount: int) -> None:
        quotas.settle_storage(session, reservation_id=reservation_id, actual_amount=actual_amount)

    def release_storage(self, session: Session, *, reservation_id: UUID) -> None:
        quotas.release_storage(session, reservation_id=reservation_id)


def assemble_storage_quota_ledger() -> StorageQuotaLedger:
    """The identity-backed ledger the resource module reserves storage against."""
    return IdentityStorageQuotaLedger()


__all__ = ["IdentityStorageQuotaLedger", "assemble_storage_quota_ledger"]
