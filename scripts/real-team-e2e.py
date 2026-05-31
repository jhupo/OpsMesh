#!/usr/bin/env python
from __future__ import annotations

import json
import logging
import os
import sys
import warnings
from dataclasses import dataclass
from uuid import uuid4

from agents import set_tracing_disabled
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.agent_runtime.openai_agents import OpenAIAgentsRunner
from backend.app.agents.models import AgentProfile
from backend.app.api.schemas.tasks import TaskCreateRequest
from backend.app.api.services.resources import WorkspaceResourceService
from backend.app.core.config import Settings, get_settings
from backend.app.db.session import SessionLocal
from backend.app.identity.models import User
from backend.app.model_providers.service import ModelProviderCredentialService
from backend.app.redis.client import redis_client
from backend.app.redis.keys import RedisKeyBuilder
from backend.app.runs.models import AgentRun
from backend.app.secrets.service import SecretEncryptionService
from backend.app.tasks.models import Task, TaskMessage, TaskStep
from backend.app.teams.models import AgentTeam, AgentTeamMember
from backend.app.workers.handlers import WorkerJobHandler
from backend.app.workers.queue import RedisQueue, consume_once
from backend.app.workspaces.models import Workspace, WorkspaceMember


@dataclass(frozen=True)
class RealTeamE2EConfig:
    api_key: str
    base_url: str
    model: str
    cleanup: bool = True
    max_jobs: int = 12
    queue_prefix: str = "real_e2e"
    disable_tracing: bool = True


def main() -> int:
    _configure_process_output()
    config = _config_from_env()
    if config.disable_tracing:
        set_tracing_disabled(True)

    settings = get_settings()
    suffix = uuid4().hex[:10]
    queue = RedisQueue(
        redis=redis_client,
        keys=RedisKeyBuilder(settings.redis_key_prefix),
        queue_name=f"{config.queue_prefix}_{suffix}",
        blocking_timeout_seconds=0,
    )
    session = SessionLocal()
    workspace: Workspace | None = None
    user: User | None = None
    exit_code = 1
    try:
        user, workspace = _seed_workspace(session, suffix)
        team = _seed_team(
            session=session,
            settings=settings,
            config=config,
            user=user,
            workspace=workspace,
        )
        task = WorkspaceResourceService(session, settings).create_task(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            data=_task_request(team),
            queue=queue,
        )
        _consume_until_done(
            session=session,
            settings=settings,
            queue=queue,
            workspace=workspace,
            task=task,
            max_jobs=config.max_jobs,
        )
        summary = _summary(session, queue, workspace, task)
        print(json.dumps(summary, ensure_ascii=False))
        exit_code = 0 if summary["status"] == "ok" else 1
    except Exception as exc:
        session.rollback()
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "workspace_id": str(workspace.id) if workspace is not None else None,
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        exit_code = 1
    finally:
        if config.cleanup:
            _cleanup(session, queue, workspace, user)
        session.close()
    return exit_code


def _configure_process_output() -> None:
    logging.getLogger("agents").setLevel(logging.ERROR)
    logging.getLogger("openai.agents").setLevel(logging.ERROR)
    warnings.filterwarnings(
        "ignore",
        message=r"RunState context was serialized from a dataclass.*",
    )


def _config_from_env() -> RealTeamE2EConfig:
    api_key = os.environ.get("CHAINCLOUD_REAL_E2E_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("CHAINCLOUD_REAL_E2E_BASE_URL")
    model = os.environ.get("CHAINCLOUD_REAL_E2E_MODEL", "gpt-5.5")
    missing = [
        name
        for name, value in {
            "CHAINCLOUD_REAL_E2E_API_KEY or OPENAI_API_KEY": api_key,
            "CHAINCLOUD_REAL_E2E_BASE_URL": base_url,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")
    return RealTeamE2EConfig(
        api_key=str(api_key),
        base_url=str(base_url),
        model=model,
        cleanup=_bool_env("CHAINCLOUD_REAL_E2E_CLEANUP", default=True),
        max_jobs=_int_env("CHAINCLOUD_REAL_E2E_MAX_JOBS", default=12),
        queue_prefix=os.environ.get("CHAINCLOUD_REAL_E2E_QUEUE_PREFIX", "real_e2e"),
        disable_tracing=_bool_env("CHAINCLOUD_REAL_E2E_DISABLE_TRACING", default=True),
    )


def _seed_workspace(session: Session, suffix: str) -> tuple[User, Workspace]:
    user = User(
        email=f"real-e2e-{suffix}@example.com",
        display_name="Real E2E Owner",
    )
    workspace = Workspace(
        owner=user,
        name=f"Real E2E {suffix}",
        slug=f"real-e2e-{suffix}",
        settings={},
    )
    session.add_all(
        [
            user,
            workspace,
            WorkspaceMember(workspace=workspace, user=user, role="owner"),
        ]
    )
    session.commit()
    return user, workspace


def _seed_team(
    *,
    session: Session,
    settings: Settings,
    config: RealTeamE2EConfig,
    user: User,
    workspace: Workspace,
) -> AgentTeam:
    credential = ModelProviderCredentialService(
        session,
        SecretEncryptionService(
            secret=settings.credential_encryption_secret,
            key_id=settings.credential_encryption_key_id,
        ),
    ).create(
        workspace_id=workspace.id,
        created_by_user_id=user.id,
        name="Real E2E temporary provider",
        provider="openai-compatible",
        api_key=config.api_key,
        default_model=config.model,
        base_url=config.base_url,
        is_default=True,
    )
    shared_model_settings = {"temperature": 0, "max_tokens": 80}
    manager = AgentProfile(
        workspace_id=workspace.id,
        name="Real PM",
        role="project_manager",
        instructions=(
            "Coordinate the team. Keep every response under 40 words. "
            "For final review, approve concise completed work when all steps are done."
        ),
        model=config.model,
        model_provider_credential_id=credential.id,
        model_settings=shared_model_settings,
    )
    researcher = AgentProfile(
        workspace_id=workspace.id,
        name="Real Researcher",
        role="researcher",
        instructions="Return a short factual research summary under 40 words.",
        model=config.model,
        model_provider_credential_id=credential.id,
        model_settings=shared_model_settings,
    )
    analyst = AgentProfile(
        workspace_id=workspace.id,
        name="Real Analyst",
        role="analyst",
        instructions="Return a short analysis under 40 words.",
        model=config.model,
        model_provider_credential_id=credential.id,
        model_settings=shared_model_settings,
    )
    session.add_all([manager, researcher, analyst])
    session.flush()
    team = AgentTeam(
        workspace_id=workspace.id,
        name="Real Market Team",
        team_type="research",
        manager_agent_profile_id=manager.id,
    )
    session.add(team)
    session.flush()
    session.add_all(
        [
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=researcher.id,
                team_role="researcher",
                department="Research",
                skill_weights={"market_research": 1.0},
                order_index=1,
            ),
            AgentTeamMember(
                workspace_id=workspace.id,
                agent_team_id=team.id,
                agent_profile_id=analyst.id,
                team_role="analyst",
                department="Analysis",
                skill_weights={"analysis": 1.0},
                order_index=2,
            ),
        ]
    )
    session.commit()
    return team


def _task_request(team: AgentTeam) -> TaskCreateRequest:
    return TaskCreateRequest(
        agent_team_id=team.id,
        domain_type="market_research",
        title="Real model team dispatch smoke",
        description="Test full team dispatch with a real model provider.",
        priority=9,
        input={
            "work_packages": [
                {
                    "package_id": "real-research",
                    "title": "Real research",
                    "required_role": "researcher",
                    "required_skills": ["market_research"],
                },
                {
                    "package_id": "real-analysis",
                    "title": "Real analysis",
                    "required_role": "analyst",
                    "required_skills": ["analysis"],
                    "depends_on": ["real-research"],
                },
            ],
        },
    )


def _consume_until_done(
    *,
    session: Session,
    settings: Settings,
    queue: RedisQueue,
    workspace: Workspace,
    task: Task,
    max_jobs: int,
) -> None:
    handler = WorkerJobHandler(
        session=session,
        queue=queue,
        agent_runner=OpenAIAgentsRunner(),
        settings=settings,
    )
    handled_jobs = 0
    while consume_once(queue, handler.handle):
        handled_jobs += 1
        if handled_jobs > max_jobs:
            raise RuntimeError("Real E2E exceeded expected job count")
    session.expire_all()
    stored_task = session.get(Task, task.id)
    if stored_task is None or stored_task.workspace_id != workspace.id:
        raise RuntimeError("Real E2E task disappeared")


def _summary(
    session: Session,
    queue: RedisQueue,
    workspace: Workspace,
    task: Task,
) -> dict[str, object]:
    session.expire_all()
    stored_task = session.get(Task, task.id)
    if stored_task is None:
        return {"status": "failed", "reason": "task_missing"}
    steps = session.scalars(
        select(TaskStep).where(TaskStep.task_id == task.id).order_by(TaskStep.order_index.asc())
    ).all()
    runs = session.scalars(
        select(AgentRun).where(AgentRun.task_id == task.id).order_by(AgentRun.created_at.asc())
    ).all()
    messages = session.scalars(
        select(TaskMessage)
        .where(TaskMessage.task_id == task.id)
        .order_by(TaskMessage.sequence.asc())
    ).all()
    dead_letters = queue.count_dead_letters(workspace_id=workspace.id)
    queued_jobs = queue.count_queued(workspace_id=workspace.id)
    completed = stored_task.status == "completed"
    return {
        "status": "ok" if completed and dead_letters == 0 and queued_jobs == 0 else "failed",
        "workspace_id": str(workspace.id),
        "task_id": str(task.id),
        "task_status": stored_task.status,
        "handled_jobs": len(runs),
        "queued_jobs_remaining": queued_jobs,
        "dead_letters": dead_letters,
        "step_statuses": [
            {
                "work_package_id": step.work_package_id,
                "status": step.status,
                "agent_profile_id": str(step.assigned_agent_profile_id)
                if step.assigned_agent_profile_id
                else None,
            }
            for step in steps
        ],
        "run_statuses": [run.status for run in runs],
        "message_types": [message.message_type for message in messages],
        "pm_acceptance": (
            stored_task.final_output.get("pm_acceptance")
            if isinstance(stored_task.final_output, dict)
            else None
        ),
    }


def _cleanup(
    session: Session,
    queue: RedisQueue,
    workspace: Workspace | None,
    user: User | None,
) -> None:
    try:
        session.rollback()
        if workspace is not None:
            session.delete(session.merge(workspace))
        if user is not None:
            session.delete(session.merge(user))
        session.commit()
    finally:
        redis_client.delete(queue.keys.queue(queue.queue_name))
        redis_client.delete(queue.keys.dead_letter_queue(queue.queue_name))


def _bool_env(name: str, *, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, *, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise RuntimeError(f"{name} must be positive")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
