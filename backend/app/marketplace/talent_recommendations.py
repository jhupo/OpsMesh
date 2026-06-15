from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.api.schemas.marketplace import (
    RoleRecommendation,
    TalentCandidateRecommendation,
    TalentListingResponse,
    TalentRecommendationRequest,
    TalentRecommendationResponse,
    TaskTalentRecommendationResponse,
)
from backend.app.marketplace.models import TalentListing
from backend.app.marketplace.recommendations import (
    RoleSpec,
    missing_work_packages_from_task,
    role_spec_for_missing_package,
    role_specs_for_request,
    score_listing,
    task_team_type,
)
from backend.app.marketplace.talent_repository import TalentMarketplaceRepository
from backend.app.tasks.message_append import TaskMessageAppendService
from backend.app.tasks.models import Task, TaskMessage


class TalentRecommendationService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = TalentMarketplaceRepository(session)

    def recommend_team(
        self,
        *,
        workspace_id: UUID,
        data: TalentRecommendationRequest,
    ) -> TalentRecommendationResponse:
        return self._recommend_for_role_specs(
            workspace_id=workspace_id,
            objective=data.objective,
            team_type=data.team_type,
            team_id=data.team_id,
            role_specs=role_specs_for_request(data),
            max_candidates_per_role=data.max_candidates_per_role,
        )

    def _recommend_for_role_specs(
        self,
        *,
        workspace_id: UUID,
        objective: str,
        team_type: str,
        team_id: UUID | None,
        role_specs: list[RoleSpec],
        max_candidates_per_role: int,
    ) -> TalentRecommendationResponse:
        existing_roles = self._repository.existing_team_roles(workspace_id, team_id)
        listings = list(
            self._session.scalars(
                select(TalentListing)
                .where(TalentListing.status == "public")
                .order_by(TalentListing.created_at.desc())
            )
        )
        recommended_roles: list[RoleRecommendation] = []
        uncovered_roles: list[str] = []

        for index, spec in enumerate(role_specs, start=1):
            scored = [
                score_listing(
                    listing,
                    role=spec.role,
                    skill_tags=spec.skill_tags,
                    capability_tags=spec.capability_tags,
                )
                for listing in listings
            ]
            viable = [item for item in scored if item.score > 0]
            viable.sort(key=lambda item: (-item.score, item.listing.created_at), reverse=False)
            candidates = [
                TalentCandidateRecommendation(
                    listing=TalentListingResponse.model_validate(item.listing),
                    score=round(item.score, 2),
                    matched_reasons=item.reasons,
                    missing_tags=item.missing_tags,
                )
                for item in viable[:max_candidates_per_role]
            ]
            if spec.role not in existing_roles:
                uncovered_roles.append(spec.role)
            recommended_roles.append(
                RoleRecommendation(
                    role=spec.role,
                    team_role=spec.team_role,
                    priority=index,
                    reason=spec.reason,
                    candidates=candidates,
                )
            )

        return TalentRecommendationResponse(
            objective=objective,
            team_type=team_type,
            recommended_roles=recommended_roles,
            existing_team_roles=sorted(existing_roles),
            uncovered_roles=uncovered_roles,
        )

    def recommend_for_task_staffing(
        self,
        *,
        workspace_id: UUID,
        task_id: UUID,
        max_candidates_per_role: int = 3,
        persist_message: bool = True,
    ) -> TaskTalentRecommendationResponse | None:
        task = self._session.get(Task, task_id)
        if task is None or task.workspace_id != workspace_id:
            return None
        missing_packages = missing_work_packages_from_task(task)
        role_specs = [role_spec_for_missing_package(package) for package in missing_packages]
        response = self._recommend_for_role_specs(
            workspace_id=workspace_id,
            objective=task.title,
            team_type=task_team_type(task),
            team_id=task.agent_team_id,
            role_specs=role_specs,
            max_candidates_per_role=max_candidates_per_role,
        )
        task_response = TaskTalentRecommendationResponse(
            task_id=task.id,
            objective=response.objective,
            team_type=response.team_type,
            recommended_roles=response.recommended_roles,
            existing_team_roles=response.existing_team_roles,
            uncovered_roles=response.uncovered_roles,
            missing_work_packages=missing_packages,
        )
        if persist_message:
            self._append_hr_recommendation_message(task, task_response)
            self._session.commit()
        return task_response

    def _append_hr_recommendation_message(
        self,
        task: Task,
        response: TaskTalentRecommendationResponse,
    ) -> TaskMessage:
        return TaskMessageAppendService(self._session).append_for_task(
            task,
            message_type="hr.staffing_recommendation",
            body=f"HR found {len(response.missing_work_packages)} staffing gap(s).",
            payload={
                "missing_work_packages": response.missing_work_packages,
                "recommended_roles": [
                    recommendation.model_dump(mode="json")
                    for recommendation in response.recommended_roles
                ],
                "uncovered_roles": response.uncovered_roles,
            },
        )

