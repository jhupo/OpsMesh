"""Tools, skills, MCP endpoints and knowledge catalog contracts."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import Field

from opsmesh.framework.agents import ToolBinding
from opsmesh.framework.typing import OpsMeshContract
from opsmesh.modules.workspaces.api import WorkspaceArtifact

ToolCallback = Callable[[dict[str, Any]], Awaitable[Any]]
ToolAuthorization = Callable[[str, str], Awaitable[None]]


class ToolDefinition(OpsMeshContract):
    name: str
    version: str
    description: str = ""
    input_schema: dict[str, Any]
    code_ref: str
    side_effects: bool = False
    timeout_seconds: float = Field(default=60, gt=0)
    allowed_principals: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


class CodeToolRegistry(Protocol):
    def register(self, code_ref: str, callback: ToolCallback) -> None: ...
    def register_runtime(self, code_ref: str, callback: ToolCallback) -> None: ...
    def unregister_runtime(self, code_ref: str) -> None: ...
    def resolve(self, code_ref: str) -> ToolCallback: ...
    def freeze(self) -> None: ...


class ToolInvoker(Protocol):
    async def invoke(
        self, tool_ref: str, arguments: dict[str, Any], *, principal_id: str, call_id: str
    ) -> Any: ...


class CapabilityCatalog(Protocol):
    async def register_tool(self, definition: ToolDefinition) -> ToolDefinition: ...
    async def get_tool(self, ref: str) -> ToolDefinition: ...
    async def list_tools(self) -> tuple[ToolDefinition, ...]: ...


class SkillDefinition(OpsMeshContract):
    name: str
    version: str
    description: str
    files: tuple[WorkspaceArtifact, ...]
    digest: str
    source_repository: str | None = None
    source_commit: str | None = None

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


class GitSkillSource(OpsMeshContract):
    repository: str
    commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    subdirectory: str = "."


class ScriptToolSource(OpsMeshContract):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,100}$")
    version: str = Field(min_length=1)
    description: str = ""
    entrypoint: str
    input_schema: dict[str, Any]
    wheels: tuple[str, ...] = ()
    side_effects: bool = True
    timeout_seconds: float = Field(default=60, gt=0, le=3600)


class ScriptToolDefinition(ScriptToolSource):
    files: tuple[WorkspaceArtifact, ...]
    digest: str

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.version}"


class ScriptTools(Protocol):
    async def publish(
        self, source: Path, declaration: ScriptToolSource
    ) -> ScriptToolDefinition: ...
    async def get(self, reference: str) -> ScriptToolDefinition: ...
    async def execute(
        self, reference: str, arguments: dict[str, Any], *, principal_id: str
    ) -> Any: ...


class SkillCatalog(Protocol):
    async def publish(
        self, name: str, version: str, source: Path, *, description: str = ""
    ) -> SkillDefinition: ...
    async def publish_git(
        self, name: str, version: str, source: GitSkillSource, *, description: str = ""
    ) -> SkillDefinition: ...
    async def get(self, ref: str) -> SkillDefinition: ...
    async def load(
        self, ref: str, workspace_id: str, *, target: str = ".agents"
    ) -> tuple[WorkspaceArtifact, ...]: ...
    async def prepare(
        self, refs: tuple[str, ...], workspace_id: str, *, mode: Literal["eager", "lazy"]
    ) -> tuple[SkillDefinition, ...]: ...


class MCPServerDefinition(OpsMeshContract):
    name: str
    version: str
    transport: Literal["streamable_http", "sse", "stdio"]
    execution_location: Literal["remote", "host"]
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    environment_secret_refs: dict[str, str] = Field(default_factory=dict)
    header_secret_refs: dict[str, str] = Field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()
    timeout_seconds: float = Field(default=30, gt=0)


class KnowledgeDocument(OpsMeshContract):
    id: str
    collection: str
    text: str
    title: str = ""


class KnowledgeSearchHit(OpsMeshContract):
    document: KnowledgeDocument
    score: float
    lexical_score: float = 0
    vector_score: float = 0


class EmbeddingProvider(Protocol):
    """A versioned vector space. Implementations preserve identity and dimension."""

    id: str
    dimensions: int

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


class MCPServerCatalog(Protocol):
    async def register(self, definition: MCPServerDefinition) -> MCPServerDefinition: ...
    async def get(self, ref: str) -> MCPServerDefinition: ...


class MCPClient(Protocol):
    async def discover(
        self, definition: MCPServerDefinition, *, principal_id: str
    ) -> tuple[ToolDefinition, ...]: ...

    async def call(
        self,
        definition: MCPServerDefinition,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        principal_id: str,
    ) -> Any: ...


class KnowledgeIndex(Protocol):
    def register_embedding_provider(
        self, provider: EmbeddingProvider, *, default: bool = False
    ) -> None: ...
    def freeze(self) -> None: ...
    async def add(self, document: KnowledgeDocument, *, principal_id: str) -> None: ...
    async def search(
        self,
        collection: str,
        query: str,
        *,
        principal_id: str,
        limit: int = 10,
        mode: Literal["lexical", "vector", "hybrid"] = "hybrid",
    ) -> tuple[KnowledgeSearchHit, ...]: ...


class AgentResourceRequest(OpsMeshContract):
    skill_refs: tuple[str, ...] = ()
    skill_loading: Literal["eager", "lazy"] = "lazy"
    mcp_refs: tuple[str, ...] = ()
    knowledge_collections: tuple[str, ...] = ()
    workspace_id: str | None = None


class PreparedAgentResources(OpsMeshContract):
    instructions: str = ""
    tools: tuple[ToolBinding, ...] = ()


class AgentResources(Protocol):
    async def prepare(
        self,
        request: AgentResourceRequest,
        *,
        principal_id: str,
        run_id: str,
        lifecycle_id: str | None = None,
    ) -> PreparedAgentResources: ...
    async def release(
        self, *, principal_id: str, run_id: str, lifecycle_id: str | None = None
    ) -> None: ...
