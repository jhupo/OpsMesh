"""Turn an agent's resource bindings into versioned, authorized gateway tools."""

import hashlib
import re
from typing import Any

from opsmesh.framework.agents import ToolBinding
from opsmesh.framework.errors import OpsMeshError
from opsmesh.modules.capabilities.api import (
    AgentResourceRequest,
    CapabilityCatalog,
    CodeToolRegistry,
    KnowledgeIndex,
    MCPClient,
    MCPServerCatalog,
    PreparedAgentResources,
    SkillCatalog,
    ToolAuthorization,
    ToolCallback,
    ToolDefinition,
)
from opsmesh.modules.workspaces.api import WorkspaceService

_RESOURCE_SCHEMA_VERSION = "2"


class AgentResourceCompiler:
    def __init__(
        self,
        catalog: CapabilityCatalog,
        code: CodeToolRegistry,
        skills: SkillCatalog,
        mcp_catalog: MCPServerCatalog,
        mcp: MCPClient,
        knowledge: KnowledgeIndex,
        workspaces: WorkspaceService,
        authorize: ToolAuthorization,
    ) -> None:
        self._catalog = catalog
        self._code = code
        self._skills = skills
        self._mcp_catalog = mcp_catalog
        self._mcp = mcp
        self._knowledge = knowledge
        self._workspaces = workspaces
        self._authorize = authorize
        self._principals: dict[str, str] = {}
        self._lifecycle_scopes: dict[tuple[str, str, str | None], set[str]] = {}
        self._scope_owners: dict[str, set[tuple[str, str, str | None]]] = {}
        self._scope_callbacks: dict[str, set[str]] = {}

    async def prepare(
        self,
        request: AgentResourceRequest,
        *,
        principal_id: str,
        run_id: str,
        lifecycle_id: str | None = None,
    ) -> PreparedAgentResources:
        scope = hashlib.sha256(
            f"{_RESOURCE_SCHEMA_VERSION}:{principal_id}:{run_id}:{request.model_dump_json()}".encode()
        ).hexdigest()[:24]
        self._principals[scope] = principal_id
        owner = (principal_id, run_id, lifecycle_id)
        self._lifecycle_scopes.setdefault(owner, set()).add(scope)
        self._scope_owners.setdefault(scope, set()).add(owner)
        tools: list[ToolBinding] = []
        instructions: list[str] = []
        if request.skill_refs:
            skill_tools, skill_instructions = await self._prepare_skills(
                request, principal_id, scope
            )
            tools.extend(skill_tools)
            instructions.append(skill_instructions)
        tools.extend(await self._prepare_mcp(request, principal_id, scope))
        if request.knowledge_collections:
            tools.append(await self._prepare_knowledge(request, principal_id, scope))
        return PreparedAgentResources(instructions="\n\n".join(instructions), tools=tuple(tools))

    async def release(
        self, *, principal_id: str, run_id: str, lifecycle_id: str | None = None
    ) -> None:
        """Release this attempt's closures without revoking a newer attempt's shared bindings."""
        owner = (principal_id, run_id, lifecycle_id)
        for scope in self._lifecycle_scopes.pop(owner, set()):
            owners = self._scope_owners.get(scope, set())
            owners.discard(owner)
            if owners:
                continue
            self._scope_owners.pop(scope, None)
            self._principals.pop(scope, None)
            for code_ref in self._scope_callbacks.pop(scope, set()):
                self._code.unregister_runtime(code_ref)

    async def _register(
        self,
        name: str,
        scope: str,
        description: str,
        schema: dict[str, Any],
        callback: ToolCallback,
        *,
        timeout: float = 60,
        aliases: tuple[str, ...] = (),
        resource_ref: str | None = None,
        source_name: str | None = None,
    ) -> ToolBinding:
        code_ref = f"resource:{name}:{scope}"
        self._scope_callbacks.setdefault(scope, set()).add(code_ref)
        try:
            self._code.resolve(code_ref)
        except OpsMeshError as error:
            if error.code != "NOT_FOUND":
                raise
            self._code.register_runtime(code_ref, callback)
        definition = ToolDefinition(
            name=name,
            version=scope,
            description=description,
            input_schema=schema,
            code_ref=code_ref,
            timeout_seconds=timeout,
            allowed_principals=(self._principals[scope],),
        )
        await self._catalog.register_tool(definition)
        return ToolBinding(
            aliases=aliases,
            resource_ref=resource_ref,
            source_name=source_name,
            name=name,
            tool_ref=definition.ref,
            description=description,
            input_schema=schema,
            timeout_seconds=timeout,
        )

    async def _prepare_skills(
        self, request: AgentResourceRequest, principal: str, scope: str
    ) -> tuple[list[ToolBinding], str]:
        if request.workspace_id is None:
            raise OpsMeshError("CONFIG_INVALID", "Skill bindings require a workspace")
        for ref in request.skill_refs:
            await self._authorize(principal, f"skill:{ref}")
        workspace = request.workspace_id
        definitions = await self._skills.prepare(
            request.skill_refs, workspace, mode=request.skill_loading
        )
        known = {definition.ref: definition for definition in definitions}
        declared_paths = {
            ref: sorted({item.path for item in definition.files})
            for ref, definition in known.items()
        }

        async def load(arguments: dict[str, Any]) -> Any:
            ref = str(arguments["skill_ref"])
            if ref not in known:
                raise OpsMeshError("AUTHORIZATION_DENIED", "Skill is not bound to this run")
            await self._authorize(principal, f"skill:{ref}")
            await self._skills.load(ref, workspace)
            return {
                "skill_ref": ref,
                "path": f".agents/{known[ref].name}/SKILL.md",
                "skill_relative_entry": "SKILL.md",
                "declared_assets": declared_paths[ref],
                "read_skill_arguments": {"skill_ref": ref, "path": "SKILL.md"},
                "path_format": (
                    "The path field above is workspace-relative and is informational only. "
                    "For read_skill, use skill_relative_entry or a declared_assets value exactly "
                    "as returned: paths are relative to the selected skill root, without any "
                    "workspace, .agents/<name>/, or skills/<name>/ prefix."
                ),
            }

        async def read(arguments: dict[str, Any]) -> Any:
            ref, path = str(arguments["skill_ref"]), str(arguments["path"])
            if ref not in known or path not in declared_paths[ref]:
                raise OpsMeshError(
                    "AUTHORIZATION_DENIED", "Skill asset is not in the selected manifest"
                )
            await self._authorize(principal, f"skill:{ref}")
            await self._skills.load(ref, workspace)
            content = await self._workspaces.read(workspace, f".agents/{known[ref].name}/{path}")
            return {"path": path, "text": content.decode("utf-8", errors="replace")}

        properties: dict[str, Any] = {"skill_ref": {"type": "string", "enum": list(known)}}
        tools = [
            await self._register(
                "load_skill",
                scope,
                "Load a selected skill and return its skill-relative entry and declared assets",
                {
                    "type": "object",
                    "properties": properties,
                    "required": ["skill_ref"],
                    "additionalProperties": False,
                },
                load,
            ),
            await self._register(
                "read_skill",
                scope,
                "Read a declared asset using its exact path relative to the selected skill root",
                {
                    "type": "object",
                    "properties": {
                        **properties,
                        "path": {
                            "type": "string",
                            "enum": sorted(
                                {path for paths in declared_paths.values() for path in paths}
                            ),
                            "description": (
                                "Exact path relative to the selected skill root, for example "
                                "SKILL.md. Use load_skill.skill_relative_entry or one of that "
                                "skill's declared_assets. Do not use a workspace path, absolute "
                                "path, .agents/<name>/ prefix, skills/<name>/ prefix, or ../. "
                                "The asset must belong to the selected skill_ref."
                            ),
                        },
                    },
                    "required": ["skill_ref", "path"],
                    "additionalProperties": False,
                },
                read,
            ),
        ]
        lines = [
            "Available skills: use load_skill and read_skill to inspect the selected instructions. "
            "Pass load_skill.read_skill_arguments directly to read_skill for the entry document; "
            "other asset paths must be from that skill's declared_assets, relative to its root."
        ]
        lines.extend(f"- {item.ref}: {item.description}" for item in definitions)
        return tools, "\n".join(lines)

    async def _prepare_mcp(
        self, request: AgentResourceRequest, principal: str, scope: str
    ) -> list[ToolBinding]:
        bindings: list[ToolBinding] = []
        for ref in request.mcp_refs:
            definition = await self._mcp_catalog.get(ref)
            for tool in await self._mcp.discover(definition, principal_id=principal):

                async def invoke(
                    arguments: dict[str, Any], server: Any = definition, tool_name: str = tool.name
                ) -> Any:
                    return await self._mcp.call(
                        server, tool_name, arguments, principal_id=principal
                    )

                suffix = hashlib.sha256(f"{ref}:{tool.name}".encode()).hexdigest()[:8]
                name = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp_{definition.name}_{tool.name}")[:52]
                bindings.append(
                    await self._register(
                        f"{name}_{suffix}",
                        scope,
                        tool.description,
                        tool.input_schema,
                        invoke,
                        timeout=tool.timeout_seconds,
                        aliases=(f"mcp:{ref}/{tool.name}",),
                        resource_ref=ref,
                        source_name=tool.name,
                    )
                )
        return bindings

    async def _prepare_knowledge(
        self, request: AgentResourceRequest, principal: str, scope: str
    ) -> ToolBinding:
        async def search(arguments: dict[str, Any]) -> Any:
            collection = str(arguments["collection"])
            if collection not in request.knowledge_collections:
                raise OpsMeshError("AUTHORIZATION_DENIED", "Collection is not bound to this run")
            hits = await self._knowledge.search(
                collection,
                str(arguments["query"]),
                principal_id=principal,
                limit=int(arguments.get("limit", 5)),
            )
            return [hit.model_dump(mode="json") for hit in hits]

        return await self._register(
            "search_knowledge",
            scope,
            "Search selected knowledge collections",
            {
                "type": "object",
                "properties": {
                    "collection": {"type": "string", "enum": list(request.knowledge_collections)},
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["collection", "query"],
                "additionalProperties": False,
            },
            search,
        )
