"""Reserve before a model call and settle only confirmed usage."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from opsmesh.platform.billing.contracts import ModelPricing
from opsmesh.platform.billing.models import ModelCharge, SpendingAccount, SpendingLimit
from opsmesh.shared.errors import ConflictError, ForbiddenError, InvalidArgumentError


def lock_account(session: Session, user_id: UUID) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"spending:{user_id}"},
    )


def reserve(
    session: Session,
    *,
    operation_id: str,
    run_id: UUID,
    user_id: UUID,
    provider_id: str | None,
    model_id: str | None,
    pricing: ModelPricing | None,
    input_bound: int,
    output_bound: int,
    attempts: int,
) -> None:
    lock_account(session, user_id)
    limits = list(
        session.scalars(
            select(SpendingLimit).where(
                SpendingLimit.user_id == user_id, SpendingLimit.amount.is_not(None)
            )
        )
    )
    if limits and (pricing is None or all(limit.currency != pricing.currency for limit in limits)):
        raise ForbiddenError(
            "A spending limit requires a model price in the budget currency",
            code="model_price_required",
        )
    period = datetime.now(UTC).strftime("%Y-%m")
    charge = ModelCharge(
        operation_id=operation_id,
        run_id=run_id,
        user_id=user_id,
        provider_id=provider_id,
        model_id=model_id,
        period=period,
        reserved=Decimal(0),
        status="reserved",
        pricing=pricing.model_dump(mode="json") if pricing else None,
        currency=pricing.currency if pricing else None,
    )
    if pricing is not None:
        amount = pricing.cost(input_bound, output_bound) * attempts
        key = (user_id, pricing.currency, period)
        account = session.get(SpendingAccount, key, with_for_update=True)
        if account is None:
            account = SpendingAccount(
                user_id=user_id,
                currency=pricing.currency,
                period=period,
                spent=Decimal(0),
                reserved=Decimal(0),
            )
            session.add(account)
        limit = session.get(SpendingLimit, (user_id, pricing.currency))
        if (
            limit is not None
            and limit.amount is not None
            and account.spent + account.reserved + amount > limit.amount
        ):
            raise ForbiddenError(
                "The monthly spending budget is exhausted", code="spending_limit_exceeded"
            )
        account.reserved += amount
        charge.reserved = amount
    session.add(charge)
    session.flush()


def settle(
    session: Session,
    operation_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    released: bool = False,
) -> None:
    charge = session.get(ModelCharge, operation_id)
    if charge is None:
        raise ConflictError("The model operation has no monetary receipt")
    lock_account(session, charge.user_id)
    session.refresh(charge, with_for_update=True)
    if charge.status != "reserved":
        if (charge.input_tokens, charge.output_tokens) != (input_tokens, output_tokens):
            raise ConflictError("The operation already has different confirmed usage")
        return
    if input_tokens < 0 or output_tokens < 0 or (released and (input_tokens or output_tokens)):
        raise InvalidArgumentError("Confirmed usage is inconsistent")
    charge.input_tokens, charge.output_tokens = input_tokens, output_tokens
    charge.status = "released" if released else "settled"
    if charge.pricing is not None:
        pricing = ModelPricing.model_validate(charge.pricing)
        amount = Decimal(0) if released else pricing.cost(input_tokens, output_tokens)
        account = session.get(
            SpendingAccount, (charge.user_id, charge.currency, charge.period), with_for_update=True
        )
        if account is None or account.reserved < charge.reserved:
            raise ConflictError("The monetary reservation ledger is inconsistent")
        account.reserved -= charge.reserved
        account.spent += amount
        charge.amount = amount
    session.flush()
