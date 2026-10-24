"""Price snapshots, independent of persistence and model transports."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ModelPricing(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    input_per_million: Decimal = Field(ge=0, max_digits=18, decimal_places=8, allow_inf_nan=False)
    output_per_million: Decimal = Field(ge=0, max_digits=18, decimal_places=8, allow_inf_nan=False)

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token usage cannot be negative")
        return (
            (self.input_per_million * input_tokens + self.output_per_million * output_tokens)
            / Decimal(1_000_000)
        ).quantize(Decimal("0.00000001"))


class ModelCostRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider_id: str | None = None
    model_id: str | None = None
    pricing: ModelPricing | None = None
    input_bound: int = Field(ge=0)
    output_bound: int = Field(ge=0)
    attempts: int = Field(default=1, ge=1, le=11)
