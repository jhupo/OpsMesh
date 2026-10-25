from collections.abc import Callable
from dataclasses import replace
from typing import Any

from agents import Model, ModelProvider, MultiProvider
from agents.models.multi_provider import MultiProviderMap
from openai.types.shared import Reasoning
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from opsmesh.infra.database import now
from opsmesh.shared.errors import ConflictError, QuotaExceededError
from opsmesh.users.service import identity

from .counting import InputTokenCounter
from .models import TokenBudget, TokenReservation, UserQuota


def quota(db: Session, user_id: str) -> UserQuota:
    db.execute(insert(UserQuota).values(user_id=user_id).on_conflict_do_nothing())
    row = db.get(UserQuota, user_id, with_for_update=True)
    assert row is not None
    return row


def reserve(db: Session, user_id: str, task_id: str, amount: int) -> str:
    if amount < 1:
        raise ValueError("Token reservation must be positive")
    identity(db, user_id)
    limits = quota(db, user_id)
    period = now().strftime("%Y-%m")
    db.execute(insert(TokenBudget).values(user_id=user_id, period=period).on_conflict_do_nothing())
    budget = db.get(TokenBudget, (user_id, period), with_for_update=True)
    assert budget is not None
    if budget.consumed + budget.reserved + amount > limits.monthly_tokens:
        raise QuotaExceededError("Monthly token budget exhausted")
    budget.reserved += amount
    row = TokenReservation(user_id=user_id, task_id=task_id, period=period, amount=amount)
    db.add(row)
    db.flush()
    return row.id


def settle(db: Session, reservation_id: str, actual: int) -> None:
    if actual < 0:
        raise ValueError("Usage cannot be negative")
    row = db.get(TokenReservation, reservation_id, with_for_update=True)
    if row is None or row.status == "settled":
        raise ConflictError("Reservation is absent or already settled")
    budget = db.get(TokenBudget, (row.user_id, row.period), with_for_update=True)
    assert budget is not None
    budget.reserved -= row.amount
    budget.consumed += actual
    row.actual, row.status = actual, "settled"


class MeteredModel(Model):
    def __init__(
        self,
        inner: Model,
        sessions: sessionmaker,
        user_id: str,
        task_id: str,
        reservation: int,
        authorize: Callable[[Session], None],
        counter: InputTokenCounter,
        reasoning: Reasoning | None = None,
    ):
        self.inner, self.sessions = inner, sessions
        self.user_id, self.task_id, self.reservation = user_id, task_id, reservation
        self.authorize = authorize
        self.counter = counter
        self.reasoning = reasoning

    def _reserve(self, amount: int) -> str:
        with self.sessions.begin() as db:
            self.authorize(db)
            return reserve(db, self.user_id, self.task_id, amount)

    def _settle(self, reservation_id: str, actual: int) -> None:
        with self.sessions.begin() as db:
            settle(db, reservation_id, actual)

    async def get_response(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._bound_output(args, kwargs)
        reservation_id = await self._prepare(args, kwargs)
        response = await self.inner.get_response(*args, **kwargs)
        if self._reported_total(response.raw_usage):
            self._settle(reservation_id, response.usage.total_tokens)
        return response

    async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
        args, kwargs = self._bound_output(args, kwargs)
        reservation_id = await self._prepare(args, kwargs)
        async for event in self.inner.stream_response(*args, **kwargs):
            if (
                event.type == "response.completed"
                and event.response.usage is not None
                and "total_tokens" in event.response.usage.model_fields_set
                and type(event.response.usage.total_tokens) is int
                and event.response.usage.total_tokens >= 0
            ):
                self._settle(reservation_id, event.response.usage.total_tokens)
            yield event

    async def _prepare(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        with self.sessions.begin() as db:
            self.authorize(db)
        input_tokens = await self.counter.count(self.inner, args, kwargs)
        settings = args[2] if len(args) > 2 else kwargs["model_settings"]
        if settings.max_tokens is not None and (
            type(settings.max_tokens) is not int or settings.max_tokens < 1
        ):
            raise ValueError("ModelSettings.max_tokens must be a positive integer")
        max_tokens = settings.max_tokens
        assert max_tokens is not None
        return self._reserve(input_tokens + max_tokens)

    async def close(self) -> None:
        await self.inner.close()

    async def _cleanup_on_run_end(self, owner: object) -> None:
        await self.inner._cleanup_on_run_end(owner)

    @staticmethod
    def _reported_total(usage: Any) -> bool:
        return (
            isinstance(usage, dict)
            and type(usage.get("total_tokens")) is int
            and usage["total_tokens"] >= 0
        )

    def get_retry_advice(self, *args: Any, **kwargs: Any) -> Any:
        return self.inner.get_retry_advice(*args, **kwargs)

    def _bound_output(
        self, args: tuple[Any, ...], kwargs: dict[str, Any]
    ) -> tuple[tuple[Any, ...], dict[str, Any]]:
        settings = args[2] if len(args) > 2 else kwargs["model_settings"]
        if {"max_tokens", "max_output_tokens", "max_completion_tokens"}.intersection(
            (settings.extra_args or {}) | (settings.extra_body or {})
        ):
            raise ValueError("Set the output limit through ModelSettings.max_tokens")
        settings = replace(
            settings,
            max_tokens=min(
                self.reservation if settings.max_tokens is None else settings.max_tokens,
                self.reservation,
            ),
            reasoning=settings.reasoning or self.reasoning,
            preserve_raw_usage=True,
            include_usage=True,
        )
        if len(args) > 2:
            args = (*args[:2], settings, *args[3:])
        else:
            kwargs["model_settings"] = settings
        return args, kwargs


class MeteredProvider(ModelProvider):
    def __init__(
        self,
        inner: ModelProvider,
        sessions: sessionmaker,
        user_id: str,
        task_id: str,
        reservation: int,
        authorize: Callable[[Session], None],
        counter: InputTokenCounter,
        default_model: str | None = None,
        reasoning: Reasoning | None = None,
    ):
        self.inner, self.sessions = inner, sessions
        self.user_id, self.task_id, self.reservation = user_id, task_id, reservation
        self.authorize = authorize
        self.counter = counter
        self.default_model, self.reasoning = default_model, reasoning

    def get_model(self, model_name: str | None) -> Model:
        selected = model_name or self.default_model
        if not selected:
            raise ValueError("Select a model in the connection, environment or invocation")
        return MeteredModel(
            self.inner.get_model(selected),
            self.sessions,
            self.user_id,
            self.task_id,
            self.reservation,
            self.authorize,
            self.counter,
            self.reasoning,
        )

    async def aclose(self) -> None:
        await self.inner.aclose()


class MeteredMultiProvider(MultiProvider):
    def __init__(self, mapping: MultiProviderMap, prefixes: frozenset[str]):
        super().__init__(provider_map=mapping)
        self.prefixes = prefixes

    def get_model(self, model_name: str | None) -> Model:
        if (
            not model_name
            or "/" not in model_name
            or model_name.partition("/")[0] not in self.prefixes
        ):
            raise ValueError("A registered provider prefix is required")
        return super().get_model(model_name)
