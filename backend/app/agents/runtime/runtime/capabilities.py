from enum import StrEnum


class SandboxCapability(StrEnum):
    SHELL = "shell"
    STDIO_MCP = "stdio_mcp"
    PROJECT_FILES = "project_files"
