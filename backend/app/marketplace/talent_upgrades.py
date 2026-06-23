from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.marketplace import (
    TalentInstallPinRequest,
    TalentInstallUpgradeRequest,
    TalentListingResponse,
    TalentUpgradeStatusResponse,
)
from backend.app.audit.service import AuditService
from backend.app.marketplace.listing_payloads import listing_agent_definition
from backend.app.marketplace.models import WorkspaceAgentInstall
from backend.app.marketplace.responses import install_response
from backend.app.marketplace.talent_repository import (
    TalentMarketplaceRepository,
    copy_agent_definition,
)


class TalentInstallUpgradeService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = TalentMarketplaceRepository(session)

    def get_upgrade_status(
        self,
        *,
        workspace_id: UUID,
        install_id: UUID,
    ) -> TalentUpgradeStatusResponse | None:
        install = self._repository.get_install(workspace_id, install_id)
        if install is None:
            return None
        latest = self._repository.latest_listing_for_install(install)
        return TalentUpgradeStatusResponse(
            install=install_response(install),
            latest_listing=TalentListingResponse.model_validate(latest) if latest else None,
            has_update=latest is not None and latest.version > install.installed_version,
            pinned_version=install.pinned_version,
        )

    def set_install_pin(
        self,
        *,
        workspace_id: UUID,
        install_id: UUID,
        data: TalentInstallPinRequest,
        user_id: UUID,
    ) -> WorkspaceAgentInstall | None:
        install = self._repository.get_install(workspace_id, install_id)
        if install is None:
            return None
        install.pinned_version = data.pinned_version
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="talent_install.pin_updated",
            target_type="workspace_agent_install",
            target_id=install.id,
            metadata={"pinned_version": data.pinned_version},
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def upgrade_install(
        self,
        *,
        workspace_id: UUID,
        install_id: UUID,
        data: TalentInstallUpgradeRequest,
        user_id: UUID,
    ) -> WorkspaceAgentInstall | None:
        install = self._repository.get_install(workspace_id, install_id)
        if install is None:
            return None
        target = self._repository.resolve_upgrade_target(install, data.target_listing_id)
        if target.version <= install.installed_version:
            raise ValueError("Talent install is already at this version or newer")
        agent = self._session.get(AgentProfile, install.installed_agent_profile_id)
        source = self._session.get(AgentProfile, target.source_agent_profile_id)
        if agent is None or source is None or source.status != "active":
            raise ValueError("Published agent profile is not available")

        definition = listing_agent_definition(target, source)
        copy_agent_definition(definition, agent)
        agent.version = target.version
        install.current_talent_listing_id = target.id
        install.source_agent_profile_id = source.id
        install.installed_version = target.version
        install.pinned_version = data.keep_pinned
        target.upgrade_count += 1
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="talent_install.upgraded",
            target_type="workspace_agent_install",
            target_id=install.id,
            metadata={
                "target_listing_id": str(target.id),
                "installed_agent_profile_id": str(agent.id),
                "version": target.version,
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

