"""Finite evaluation plans and an explicit adapter registration boundary."""

from collections.abc import Callable
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


class DatasetCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(min_length=1, max_length=120)
    input: JsonValue
    expected: JsonValue = None


class DatasetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=120)
    cases: list[DatasetCase] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_cases(self) -> "DatasetSpec":
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Dataset case identities must be unique")
        return self


class EvaluatorSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    name: str = Field(min_length=1, max_length=100)
    revision: str = Field(default="1", min_length=1, max_length=100)
    config: dict[str, JsonValue] = Field(default_factory=dict)


class EvaluationTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["agent", "workflow"]
    instance_id: UUID


class ExperimentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset_id: UUID
    dataset_version: int = Field(ge=1)
    targets: list[EvaluationTarget] = Field(min_length=1, max_length=5)
    evaluators: list[EvaluatorSpec] = Field(min_length=1, max_length=10)
    concurrency: int = Field(default=2, ge=1, le=8)
    request_key: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def unique_targets_and_evaluators(self) -> "ExperimentSpec":
        if len({(t.kind, t.instance_id) for t in self.targets}) != len(self.targets):
            raise ValueError("Evaluation targets must be unique")
        if len({e.name for e in self.evaluators}) != len(self.evaluators):
            raise ValueError("Evaluator names must be unique")
        return self


class Score(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    value: float = Field(ge=0, le=1, allow_inf_nan=False)
    reason: str = Field(default="", max_length=2000)


type Evaluator = Callable[[DatasetCase, object, dict[str, JsonValue]], Score]


class EvaluatorRegistry:
    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], Evaluator] = {}

    def register(self, name: str, revision: str, evaluator: Evaluator) -> None:
        key = name, revision
        if key in self._adapters:
            raise ValueError("An evaluator revision is already registered")
        self._adapters[key] = evaluator

    def get(self, spec: EvaluatorSpec) -> Evaluator:
        try:
            return self._adapters[(spec.name, spec.revision)]
        except KeyError:
            raise ValueError("The evaluator adapter revision is not registered") from None
