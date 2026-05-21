import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import TypeVar
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import Select, func, or_, select
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
    WorkspaceSkillUpgradeRequest,
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
from backend.app.secrets.service import SecretEncryptionService

T = TypeVar("T")


@dataclass(frozen=True)
class McpCatalogUsage:
    call_count: int
    failed_call_count: int
    last_call_at: datetime | None
    last_call_status: str | None
    last_error_code: str | None


@dataclass(frozen=True)
class McpCatalogTool:
    allowlist: McpToolAllowlist
    usage: McpCatalogUsage


@dataclass(frozen=True)
class McpCatalogServer:
    server: McpServer
    tools: list[McpCatalogTool]
    credential_count: int
    workspace_credential_count: int
    credential_status: str
    execution_mode: str
    executable: bool
    blocked_reasons: list[str]
    connection_summary: dict[str, object]
    usage: McpCatalogUsage


class CapabilityService:
    def __init__(
        self,
        session: Session,
        secret_service: SecretEncryptionService | None = None,
    ) -> None:
        self._session = session
        self._secret_service = secret_service

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

    def list_skills(
        self,
        page: PageParams,
        workspace_id: UUID | None = None,
    ) -> tuple[list[Skill], int]:
        statement = select(Skill).where(Skill.status == "active")
        if workspace_id is not None:
            statement = statement.where(
                or_(
                    Skill.visibility == "public",
                    Skill.owner_workspace_id == workspace_id,
                )
            )
        else:
            statement = statement.where(Skill.visibility == "public")
        statement = statement.order_by(Skill.key.asc())
        return self._page(statement, page)

    def create_skill(self, data: SkillCreateRequest, workspace_id: UUID | None = None) -> Skill:
        owner_workspace_id = workspace_id if data.visibility == "private" else None
        skill = Skill(owner_workspace_id=owner_workspace_id, **data.model_dump())
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
        return self.install_skill_by_id(
            workspace_id=workspace_id,
            user_id=user_id,
            skill_id=data.skill_id,
            config=data.config,
        )

    def install_skill_by_id(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        skill_id: UUID,
        config: dict[str, object] | None = None,
    ) -> WorkspaceSkillInstall:
        skill = self._require_installable_skill(workspace_id, skill_id)
        install = WorkspaceSkillInstall(
            workspace_id=workspace_id,
            skill_id=skill.id,
            installed_by_user_id=user_id,
            config=config or {},
        )
        self._copy_skill_snapshot(install, skill)
        self._session.add(install)
        flush_or_raise_conflict(self._session, "Skill is already installed in workspace")
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
                action="skill.installed",
                target_type="workspace_skill_install",
                target_id=install.id,
                metadata={
                    "skill_id": str(skill.id),
                    "installed_key": install.installed_key,
                    "installed_version": install.installed_version,
                    "source_checksum": install.source_checksum,
                },
            )
        commit_or_raise_conflict(self._session, "Skill is already installed in workspace")
        self._session.refresh(install)
        return install

    def upgrade_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
        data: WorkspaceSkillUpgradeRequest,
    ) -> WorkspaceSkillInstall:
        install = self._require_workspace_install(workspace_id, install_id)
        skill = self._require_installable_skill(workspace_id, data.skill_id)
        install.skill_id = skill.id
        self._copy_skill_snapshot(install, skill)
        if data.config is not None:
            install.config = data.config
        install.status = "active"
        install.disabled_at = None
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.upgraded",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(skill.id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def disable_skill_install(
        self,
        workspace_id: UUID,
        user_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillInstall:
        install = self._require_workspace_install(workspace_id, install_id)
        install.status = "disabled"
        install.disabled_at = datetime.now(UTC)
        AuditService(self._session).record_user_action(
            workspace_id=workspace_id,
            user_id=user_id,
            action="skill_install.disabled",
            target_type="workspace_skill_install",
            target_id=install.id,
            metadata={
                "skill_id": str(install.skill_id),
                "installed_key": install.installed_key,
                "installed_version": install.installed_version,
                "source_checksum": install.source_checksum,
                "disabled_at": install.disabled_at.isoformat(),
            },
        )
        self._session.commit()
        self._session.refresh(install)
        return install

    def list_workspace_skills(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        include_disabled: bool = False,
    ) -> tuple[list[WorkspaceSkillInstall], int]:
        statement = select(WorkspaceSkillInstall).where(
            WorkspaceSkillInstall.workspace_id == workspace_id,
        )
        if not include_disabled:
            statement = statement.where(WorkspaceSkillInstall.status == "active")
        statement = statement.order_by(
            WorkspaceSkillInstall.created_at.desc(),
            WorkspaceSkillInstall.id.desc(),
        )
        return self._page(statement, page)

    def _require_workspace_install(
        self,
        workspace_id: UUID,
        install_id: UUID,
    ) -> WorkspaceSkillInstall:
        install = self._session.get(WorkspaceSkillInstall, install_id)
        if install is None or install.workspace_id != workspace_id:
            raise ValueError("Workspace skill install not found")
        return install

    def _require_installable_skill(self, workspace_id: UUID, skill_id: UUID) -> Skill:
        skill = self._session.get(Skill, skill_id)
        if (
            skill is None
            or skill.status != "active"
            or not self._can_use_skill(workspace_id, skill)
        ):
            raise ValueError("Skill not found")
        return skill

    def _copy_skill_snapshot(self, install: WorkspaceSkillInstall, skill: Skill) -> None:
        install.installed_key = skill.key
        install.installed_name = skill.name
        install.installed_version = skill.version
        install.installed_description = skill.description
        install.installed_capability_keys = list(skill.capability_keys)
        install.installed_manifest = dict(skill.manifest)
        install.source_owner_workspace_id = skill.owner_workspace_id
        install.source_visibility = skill.visibility
        install.source_checksum = _skill_checksum(skill)

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

    def disable_mcp_server(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpServer:
        server = self._require_server(workspace_id, mcp_server_id)
        server.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_server.disabled",
                target_type="mcp_server",
                target_id=server.id,
                metadata={"name": server.name},
            )
        self._session.commit()
        self._session.refresh(server)
        return server

    def disable_mcp_tool(
        self,
        workspace_id: UUID,
        mcp_server_id: UUID,
        allowlist_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpToolAllowlist:
        self._require_server(workspace_id, mcp_server_id)
        allow = self._session.get(McpToolAllowlist, allowlist_id)
        if (
            allow is None
            or allow.workspace_id != workspace_id
            or allow.mcp_server_id != mcp_server_id
        ):
            raise ValueError("MCP tool allowlist entry not found")
        allow.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_tool.disabled",
                target_type="mcp_tool_allowlist",
                target_id=allow.id,
                metadata={
                    "mcp_server_id": str(mcp_server_id),
                    "tool_name": allow.tool_name,
                },
            )
        self._session.commit()
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

    def list_mcp_catalog(
        self,
        workspace_id: UUID,
        page: PageParams,
        agent_profile_id: UUID | None = None,
    ) -> tuple[list[McpCatalogServer], int]:
        allowed_names: set[str] | None = None
        if agent_profile_id is not None:
            agent = self._session.get(AgentProfile, agent_profile_id)
            if agent is None or agent.workspace_id != workspace_id:
                raise ValueError("Agent profile not found")
            allowed_names = self._agent_allowed_mcp_tool_names(agent)

        servers, total = self.list_mcp_servers(workspace_id, page)
        server_ids = [server.id for server in servers]
        if not server_ids:
            return [], total

        tool_statement = (
            select(McpToolAllowlist)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.mcp_server_id.in_(server_ids),
                McpToolAllowlist.status == "active",
            )
            .order_by(McpToolAllowlist.tool_name.asc())
        )
        if allowed_names is not None:
            if not allowed_names:
                tool_rows: list[McpToolAllowlist] = []
            else:
                tool_rows = list(
                    self._session.scalars(
                        tool_statement.where(McpToolAllowlist.tool_name.in_(allowed_names))
                    )
                )
        else:
            tool_rows = list(self._session.scalars(tool_statement))

        credentials = self._session.scalars(
            select(McpCredentialReference).where(
                McpCredentialReference.workspace_id == workspace_id,
                McpCredentialReference.status == "active",
                or_(
                    McpCredentialReference.mcp_server_id.in_(server_ids),
                    McpCredentialReference.mcp_server_id.is_(None),
                ),
            )
        ).all()
        usage_by_server_tool = self._mcp_usage_by_server_tool(workspace_id, server_ids)

        tools_by_server: dict[UUID, list[McpCatalogTool]] = {
            server_id: [] for server_id in server_ids
        }
        for allow in tool_rows:
            tools_by_server.setdefault(allow.mcp_server_id, []).append(
                McpCatalogTool(
                    allow,
                    usage_by_server_tool.get(
                        (allow.mcp_server_id, allow.tool_name),
                        _empty_mcp_usage(),
                    ),
                )
            )

        credential_counts: dict[UUID, int] = {server_id: 0 for server_id in server_ids}
        workspace_credential_count = 0
        for credential in credentials:
            if credential.mcp_server_id is None:
                workspace_credential_count += 1
                continue
            if credential.mcp_server_id in credential_counts:
                credential_counts[credential.mcp_server_id] += 1

        return [
            self._catalog_entry(
                server,
                tools_by_server.get(server.id, []),
                credential_counts.get(server.id, 0),
                workspace_credential_count,
                _rollup_mcp_usage(server.id, usage_by_server_tool),
            )
            for server in servers
        ], total

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
        credential = McpCredentialReference(
            workspace_id=workspace_id,
            **data.model_dump(exclude={"secret_payload"}),
        )
        if data.secret_payload is not None:
            if self._secret_service is None:
                raise ValueError("Hosted credential encryption is not configured")
            encrypted = self._secret_service.encrypt_payload(data.secret_payload)
            credential.provider = "hosted"
            credential.external_ref = ""
            credential.encrypted_secret_payload = encrypted.ciphertext
            credential.secret_fingerprint = encrypted.fingerprint
            credential.encryption_key_id = encrypted.key_id
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
                    "provider": credential.provider,
                    "has_hosted_secret": credential.encrypted_secret_payload is not None,
                },
            )
        commit_or_raise_conflict(self._session, "MCP credential name already exists")
        self._session.refresh(credential)
        return credential

    def list_credential_references(
        self,
        workspace_id: UUID,
        page: PageParams,
        *,
        mcp_server_id: UUID | None = None,
        include_disabled: bool = False,
    ) -> tuple[list[McpCredentialReference], int]:
        if mcp_server_id is not None:
            self._require_server(workspace_id, mcp_server_id)
        statement = select(McpCredentialReference).where(
            McpCredentialReference.workspace_id == workspace_id
        )
        if mcp_server_id is not None:
            statement = statement.where(McpCredentialReference.mcp_server_id == mcp_server_id)
        if not include_disabled:
            statement = statement.where(McpCredentialReference.status == "active")
        return self._page(
            statement.order_by(
                McpCredentialReference.status.asc(),
                McpCredentialReference.created_at.desc(),
            ),
            page,
        )

    def disable_credential_reference(
        self,
        workspace_id: UUID,
        credential_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> McpCredentialReference:
        credential = self._session.get(McpCredentialReference, credential_id)
        if credential is None or credential.workspace_id != workspace_id:
            raise ValueError("MCP credential reference not found")
        credential.status = "disabled"
        if actor_user_id is not None:
            AuditService(self._session).record_user_action(
                workspace_id=workspace_id,
                user_id=actor_user_id,
                action="mcp_credential.disabled",
                target_type="mcp_credential_reference",
                target_id=credential.id,
                metadata={
                    "name": credential.name,
                    "mcp_server_id": str(credential.mcp_server_id)
                    if credential.mcp_server_id is not None
                    else None,
                },
            )
        self._session.commit()
        self._session.refresh(credential)
        return credential

    def log_mcp_tool_call(
        self,
        workspace_id: UUID,
        data: McpToolCallLogRequest,
    ) -> McpToolCallLog:
        if data.mcp_server_id is not None:
            self._require_server(workspace_id, data.mcp_server_id)
        allow = self._allowed_tool_by_name(workspace_id, data.tool_name)
        if allow is None:
            raise ValueError("MCP tool is not allowed for this workspace")
        request_payload = data.request
        response_payload = data.response
        error_payload = data.error
        log = McpToolCallLog(
            workspace_id=workspace_id,
            latency_ms=_latency_ms_from_payload(response_payload, error_payload),
            argument_sha256=_hash_from_payload(request_payload, "arguments_sha256"),
            response_sha256=_response_hash_from_payload(response_payload),
            error_code=_error_code_from_payload(error_payload),
            created_at=datetime.now(UTC),
            **data.model_dump(),
        )
        self._session.add(log)
        self._session.commit()
        self._session.refresh(log)
        return log

    def _can_use_skill(self, workspace_id: UUID, skill: Skill) -> bool:
        return skill.visibility == "public" or skill.owner_workspace_id == workspace_id

    def _allowed_tool_by_name(self, workspace_id: UUID, tool_name: str) -> McpToolAllowlist | None:
        return self._session.scalar(
            select(McpToolAllowlist)
            .join(McpServer, McpServer.id == McpToolAllowlist.mcp_server_id)
            .where(
                McpToolAllowlist.workspace_id == workspace_id,
                McpToolAllowlist.tool_name == tool_name,
                McpToolAllowlist.status == "active",
                McpServer.status == "active",
            )
        )

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

    def _catalog_entry(
        self,
        server: McpServer,
        tools: list[McpCatalogTool],
        credential_count: int,
        workspace_credential_count: int,
        usage: McpCatalogUsage,
    ) -> McpCatalogServer:
        credential_status = _credential_status(
            server,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
        )
        blocked_reasons = _mcp_blocked_reasons(
            server,
            tools=tools,
            credential_status=credential_status,
        )
        return McpCatalogServer(
            server=server,
            tools=tools,
            credential_count=credential_count,
            workspace_credential_count=workspace_credential_count,
            credential_status=credential_status,
            execution_mode=_execution_mode(server),
            executable=not blocked_reasons,
            blocked_reasons=blocked_reasons,
            connection_summary=_connection_summary(server),
            usage=usage,
        )

    def _mcp_usage_by_server_tool(
        self,
        workspace_id: UUID,
        server_ids: list[UUID],
    ) -> dict[tuple[UUID, str], McpCatalogUsage]:
        logs = self._session.scalars(
            select(McpToolCallLog)
            .where(
                McpToolCallLog.workspace_id == workspace_id,
                McpToolCallLog.mcp_server_id.in_(server_ids),
            )
            .order_by(McpToolCallLog.created_at.asc())
        ).all()
        states: dict[tuple[UUID, str], _McpUsageState] = {}
        for log in logs:
            if log.mcp_server_id is None:
                continue
            key = (log.mcp_server_id, log.tool_name)
            states.setdefault(key, _McpUsageState()).add(log)
        return {key: state.to_usage() for key, state in states.items()}

    def _page(self, statement: Select[tuple[T]], page: PageParams) -> tuple[list[T], int]:
        total = self._session.scalar(
            select(func.count()).select_from(statement.order_by(None).subquery())
        )
        rows = self._session.scalars(statement.limit(page.limit).offset(page.offset)).all()
        return list(rows), int(total or 0)


def _skill_checksum(skill: Skill) -> str:
    payload = {
        "key": skill.key,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "capability_keys": skill.capability_keys,
        "manifest": skill.manifest,
        "owner_workspace_id": str(skill.owner_workspace_id)
        if skill.owner_workspace_id is not None
        else None,
        "visibility": skill.visibility,
    }
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


def _hash_from_payload(payload: dict[str, object] | None, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _response_hash_from_payload(payload: dict[str, object] | None) -> str | None:
    existing = _hash_from_payload(payload, "response_sha256")
    if existing is not None:
        return existing
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        normalized = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(normalized.encode("utf-8")).hexdigest()
    return None


def _latency_ms_from_payload(
    response: dict[str, object] | None,
    error: dict[str, object] | None,
) -> int | None:
    for payload in (response, error):
        if not isinstance(payload, dict):
            continue
        value = payload.get("latency_ms")
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _error_code_from_payload(payload: dict[str, object] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    code = payload.get("code")
    return code if isinstance(code, str) else None


@dataclass
class _McpUsageState:
    call_count: int = 0
    failed_call_count: int = 0
    last_call_at: datetime | None = None
    last_call_status: str | None = None
    last_error_code: str | None = None

    def add(self, log: McpToolCallLog) -> None:
        self.call_count += 1
        if _mcp_log_failed(log):
            self.failed_call_count += 1
        if self.last_call_at is None or log.created_at >= self.last_call_at:
            self.last_call_at = log.created_at
            self.last_call_status = log.status
            self.last_error_code = log.error_code

    def merge(self, usage: McpCatalogUsage) -> None:
        self.call_count += usage.call_count
        self.failed_call_count += usage.failed_call_count
        if usage.last_call_at is None:
            return
        if self.last_call_at is None or usage.last_call_at >= self.last_call_at:
            self.last_call_at = usage.last_call_at
            self.last_call_status = usage.last_call_status
            self.last_error_code = usage.last_error_code

    def to_usage(self) -> McpCatalogUsage:
        return McpCatalogUsage(
            call_count=self.call_count,
            failed_call_count=self.failed_call_count,
            last_call_at=self.last_call_at,
            last_call_status=self.last_call_status,
            last_error_code=self.last_error_code,
        )


def _empty_mcp_usage() -> McpCatalogUsage:
    return McpCatalogUsage(
        call_count=0,
        failed_call_count=0,
        last_call_at=None,
        last_call_status=None,
        last_error_code=None,
    )


def _rollup_mcp_usage(
    server_id: UUID,
    usage_by_server_tool: dict[tuple[UUID, str], McpCatalogUsage],
) -> McpCatalogUsage:
    state = _McpUsageState()
    for (candidate_server_id, _tool_name), usage in usage_by_server_tool.items():
        if candidate_server_id == server_id:
            state.merge(usage)
    return state.to_usage()


def _mcp_log_failed(log: McpToolCallLog) -> bool:
    return log.error_code is not None or log.status in {
        "blocked",
        "failed",
        "error",
        "timeout",
    }


def _credential_status(
    server: McpServer,
    *,
    credential_count: int,
    workspace_credential_count: int,
) -> str:
    if credential_count > 0:
        return "server_configured"
    if workspace_credential_count > 0:
        return "workspace_configured"
    if _requires_credentials(server):
        return "missing_required"
    return "not_required"


def _requires_credentials(server: McpServer) -> bool:
    configured = server.connection.get("requires_credentials")
    if isinstance(configured, bool):
        return configured
    return server.server_type.lower().strip() == "hosted"


def _execution_mode(server: McpServer) -> str:
    server_type = server.server_type.lower().strip()
    if server_type == "stdio":
        runtime = server.connection.get("runtime")
        if runtime == "self_hosted":
            return "self_hosted_stdio"
        return "isolated_runtime_stdio"
    if server_type in {"http", "https", "http_jsonrpc", "jsonrpc"}:
        return "remote_http"
    if server_type in {"sse", "http_sse"}:
        return "remote_sse"
    if server_type == "hosted":
        return "hosted"
    return "unsupported"


def _mcp_blocked_reasons(
    server: McpServer,
    *,
    tools: list[McpCatalogTool],
    credential_status: str,
) -> list[str]:
    reasons: list[str] = []
    if server.status != "active":
        reasons.append("server_inactive")
    if server.health_status == "unhealthy":
        reasons.append("server_unhealthy")
    if not tools:
        reasons.append("no_allowed_tools")
    if credential_status == "missing_required":
        reasons.append("missing_required_credentials")
    if _execution_mode(server) == "unsupported":
        reasons.append("unsupported_server_type")
    if server.server_type.lower().strip() == "stdio" and not _has_stdio_command(server):
        reasons.append("missing_stdio_command")
    remote_server_types = {"http", "https", "http_jsonrpc", "jsonrpc", "sse", "http_sse"}
    if server.server_type.lower().strip() in remote_server_types and not _has_remote_url(server):
        reasons.append("missing_remote_url")
    if server.server_type.lower().strip() == "hosted":
        transport = str(server.connection.get("transport") or "").lower().strip()
        if transport not in {"http", "https", "http_jsonrpc", "jsonrpc", "sse", "http_sse"}:
            reasons.append("unsupported_hosted_transport")
        if not _has_remote_url(server):
            reasons.append("missing_remote_url")
    return reasons


def _connection_summary(server: McpServer) -> dict[str, object]:
    connection = server.connection
    summary: dict[str, object] = {
        "requires_credentials": _requires_credentials(server),
    }
    transport = connection.get("transport")
    if isinstance(transport, str) and transport:
        summary["transport"] = transport
    url = connection.get("url") or connection.get("endpoint")
    if isinstance(url, str) and url:
        parsed = urlparse(url)
        summary["remote_host"] = parsed.netloc or None
        summary["has_remote_url"] = True
    else:
        summary["has_remote_url"] = False
    summary["has_stdio_command"] = _has_stdio_command(server)
    return summary


def _has_stdio_command(server: McpServer) -> bool:
    command = server.connection.get("command")
    if isinstance(command, str):
        return bool(command.strip())
    if isinstance(command, list):
        return any(isinstance(item, str) and bool(item.strip()) for item in command)
    return False


def _has_remote_url(server: McpServer) -> bool:
    url = server.connection.get("url") or server.connection.get("endpoint")
    return isinstance(url, str) and url.lower().startswith(("https://", "http://"))
