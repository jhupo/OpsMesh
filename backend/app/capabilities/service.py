from datetime import UTC, datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.agents.models import AgentProfile
from backend.app.api.pagination import PageParams
from backend.app.api.schemas.capabilities import (
    CapabilityCreateRequest,
    McpCredentialReferenceCreateRequest,
    McpServerCreateRequest,
    McpToolAllowRequest,
    McpToolCallLogRequest,
    SkillCreateRequest,
    ToolGroupCreateRequest,
    WorkspaceSkillInstallRequest,
)
from backend.app.audit.service import AuditService
from backend.app.capabilities.models import (
    Capability,
    McpCredentialReference,
    McpServer,
    McpToolAllowlist,
    McpToolCallLog,
    Skill,
    ToolGroup,
    WorkspaceSkillInstall,
)
from backend.app.db.errors import commit_or_raise_conflict, flush_or_raise_conflict

T = TypeVar("T")


class CapabilityService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_capabilities(
        self,
        page: PageParams,
        category: str | None = None,
    ) -> tuple[list[Capability], int]:
        statement = select(Capability).where(Capability.status == "active")
        if category is not None:
            statement = statement.where(Capability.category == category)
        return self._page(statement.order_by(Capability.category.asc(), Capability.key.asc()), page)

    def create_capability(self, data: CapabilityCreateRequest) -> Capability:
        capability = Capability(**data.model_dump())
        self._session.add(capability)
        commit_or_raise_conflict(self._session, "Capability key already exists")
        self._session.refresh(capability)
        return capability

    def list_skills(self, page: PageParams) -> tuple[list[Skill], int]:
        statement = select(Skill).where(Skill.status == "active").order_by(Skill.key.asc())
        return self._page(statement, page)

    def create_skill(self, data: SkillCreateRequest) -> Skill:
        skill = Skill(**data.model_dump())
        self._session.add(skill)
        commit_or_raise_conflict(self._session, "Skill version already exists")
        self._session.refresh(skill)
        return skill

    def install_skill(
        self,
        workspace_id: UUID,
        user_id: UUID,
        data: WorkspaceSkillInstallRequest,
    ) -> WorkspaceSkillInstall:
        skill = self._session.get(Skill, data.skill_id)
        if skill is None or skill.status != "active":
            raise ValueError("Skill not found")
        install = WorkspaceSkillInstall(
            workspace_id=workspace_id,
            skill_id=data.skill_id,
            installed_by_user_id=user_id,
            config=data.config,
        )
        self._session.add(install)
        flush_or_raise_conflict(self._session, "Skill is already installed in workspace")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill.installed",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={"skill_id": str(data.skill_id)},
        )
        commit_or_raise_conflict(self._session, "Skill is already installed in workspace")
        self._session.refresh(install)
        return install

    def list_workspace_skills(
        self,
        workspace_id: UUID,
        page: PageParams,
    ) -> tuple[list[WorkspaceSkillInstall], int]:
        statement = (
            select(WorkspaceSkillInstall)
            .where(
                WorkspaceSkillInstall.workspace_id == workspace_id,
                WorkspaceSkillInstall.status == "active",
            )
            .order_by(WorkspaceSkillInstall.created_at.desc())
        )
        return self._page(statement, page)

    def create_tool_group(self, data: ToolGroupCreateRequest) -> ToolGroup:
        group = ToolGroup(**data.model_dump())
        self._session.add(group)
        commit_or_raise_conflict(self._session, "Tool group key already exists")
        self._session.refresh(group)
        return group

    def list_tool_groups(self, page: PageParams) -> tuple[list[ToolGroup], int]:
        statement = (
            select(ToolGroup)
            .where(ToolGroup.status == "active")
            .order_by(ToolGroup.key.asc())
        )
        return self._page(statement, page)

    def create_mcp_server(
        self,
        workspace_id: UUID,
        data: McpServerCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpServer:
        server = McpServer(workspace_id=workspace_id, **data.model_dump())
        self._session.add(server)
        flush_or_raise_conflict(self._session, "MCP server name already exists")
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.created",
                target_type="mcp_server",
                target_id=server.id,
                metadata={"name": server.name, "server_type": server.server_type},
            )
        commit_or_raise_conflict(self._session, "MCP server name already exists")
        self._session.refresh(server)
        return server

    def list_mcp_servers(self, workspace_id: UUID, page: PageParams) -> tuple[list[McpServer], int]:
        statement = (
            select(McpServer)
            .where(McpServer.workspace_id == workspace_id)
            .order_by(McpServer.created_at.desc())
        )
        return self._page(statement, page)

    def allow_mcp_tool(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        data: McpToolAllowRequest,
        actor_user_id: UUID | None = None,
    ) -> McpToolAllowlist:
        self._require_server(workspace_id, mcp_server_id)
        allow = McpToolAllowlist(
            workspace_id=workspace_id,
            mcp_server_id=mcp_server_id,
            **data.model_dump(),
        )
        self._session.add(allow)
        flush_or_raise_conflict(self._session, "MCP tool is already allowed for this server")
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_tool.allowed",
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                metadata={
                    "mcp_server_id": str(mcp_server_id),
                    "tool_name": allow.tool_name,
                    "risk_level": allow.risk_level,
                },
            )
        commit_or_raise_conflict(self._session, "MCP tool is already allowed for this server")
        self._session.refresh(allow)
        return allow

    def list_allowed_mcp_tools(
        self,
        workspace_id: UUID,
    ) -> list[tuple[McpToolAllowlist, McpServer]]:
        statement = (
            select(McpToolAllowlist, McpServer)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.status == "active",
                McpServer.status == "active",
            )
            .order_by(McpServer.name.asc(), McpToolAllowlist.tool_name.asc())
        )
        return [(row[0], row[1]) for row in self._session.execute(statement).all()]

    def mcp_tools_for_agent(
        self,
        workspace_id: UUID,
        agent_profile_id: UUID,
    ) -> list[tuple[McpToolAllowlist, McpServer]]:
        agent = self._session.get(AgentProfile, agent_profile_id)
        if agent is None or agent.workspace_id != workspace_id:
            raise ValueError("Agent profile not found")
        allowed_names = self._agent_allowed_mcp_tool_names(agent)
        tools = self.list_allowed_mcp_tools(workspace_id)
        if allowed_names is None:
            return tools
        return [(allow, server) for allow, server in tools if allow.tool_name in allowed_names]

    def create_credential_reference(
        self,
        workspace_id: UUID,
        data: McpCredentialReferenceCreateRequest,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        if data.mcp_server_id is not None:
            self._require_server(workspace_id, data.mcp_server_id)
        credential = McpCredentialReference(workspace_id=workspace_id, **data.model_dump())
        self._session.add(credential)
        flush_or_raise_conflict(self._session, "MCP credential name already exists")
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_credential.created",
                target_type="mcp_credential_reference",
                target_id=credential.id,
                metadata={
                    "name": credential.name,
                    "mcp_server_id": str(credential.mcp_server_id)
                    if credential.mcp_server_id is not None
                    else None,
                },
            )
        commit_or_raise_conflict(self._session, "MCP credential name already exists")
        self._session.refresh(credential)
        return credential

    def log_mcp_tool_call(
        self,
        workspace_id: UUID,
        data: McpToolCallLogRequest,
    ) -> McpToolCallLog:
        if data.mcp_server_id is not None:
            self._require_server(workspace_id, data.mcp_server_id)
        log = McpToolCallLog(
            workspace_id=workspace_id,
            created_at=datetime.now(UTC),
            **data.model_dump(),
        )
        self._session.add(log)
        self._session.commit()
        self._session.refresh(log)
        return log

    def _require_server(self, workspace_id: UUID, server_id: UUID) -> McpServer:
        server = self._session.get(McpServer, server_id)
        if server is None or server.workspace_id != workspace_id:
            raise ValueError("MCP server not found")
        return server

    def _agent_allowed_mcp_tool_names(self, agent: AgentProfile) -> set[str] | None:
        configured = agent.tool_policy.get("mcp_tools")
        if configured in (None, "*"):
            return None
        if isinstance(configured, list) and all(isinstance(item, str) for item in configured):
            return set(configured)
        return set()

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)
