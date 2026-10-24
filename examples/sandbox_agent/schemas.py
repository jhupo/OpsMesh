from pydantic import BaseModel, ConfigDict, Field


class SandboxConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_id: str | None = None
    model: str | None = Field(default=None, min_length=1)
    instructions: str = (
        "Work in /workspace. Read inputs from input/ and put deliverables in output/."
    )
    image: str = "python:3.13-slim"
    initial_files: dict[str, str] = Field(default_factory=dict, max_length=100)
    reserve_tokens: int = Field(default=8192, ge=1)
