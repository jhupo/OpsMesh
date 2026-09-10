from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from backend.app.core.pagination import PageParams
from backend.app.db.pagination import page_scalars
from backend.app.marketplace.models import TalentListing, WorkspaceAgentInstall
from backend.app.marketplace.talent_repository import TalentMarketplaceRepository


class TalentCatalogService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = TalentMarketplaceRepository(session)

    def list_public_listings(
        self,
        page: PageParams,
        *,
        query: str | None = None,
        role: str | None = None,
        skill: str | None = None,
    ) -> tuple[list[TalentListing], int]:
        statement = select(TalentListing).where(TalentListing.status == "public")
        if query is not None:
            pattern = f"%{query}%"
            statement = statement.where(
                or_(TalentListing.title.ilike(pattern), TalentListing.summary.ilike(pattern))
            )
        if role is not None:
            statement = statement.where(TalentListing.role == role)
        if skill is not None:
            rows = self._session.scalars(statement.order_by(TalentListing.created_at.desc())).all()
            filtered = [row for row in rows if skill in row.skill_tags]
            return filtered[page.offset : page.offset + page.limit], len(filtered)
        return self._page(statement.order_by(TalentListing.created_at.desc()), page)

    def get_listing(self, listing_id: UUID) -> TalentListing | None:
        return self._repository.get_public_listing(listing_id)

    def get_install(self, workspace_id: UUID, install_id: UUID) -> WorkspaceAgentInstall | None:
        return self._repository.get_install(workspace_id, install_id)

    def list_installs(
        self, workspace_id: UUID, page: PageParams
    ) -> tuple[list[WorkspaceAgentInstall], int]:
        return self._repository.list_installs(workspace_id, page)

    def _page(
        self,
        statement: Select[tuple[TalentListing]],
        page: PageParams,
    ) -> tuple[list[TalentListing], int]:
        return page_scalars(self._session, statement, page)
