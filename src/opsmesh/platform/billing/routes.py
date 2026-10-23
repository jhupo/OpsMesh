"""Owned usage reporting and administrator-managed monthly budgets."""

from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from opsmesh.platform.audit.contracts import AuditActor, AuditRecord, record_audit
from opsmesh.platform.billing.models import ModelCharge, SpendingAccount, SpendingLimit
from opsmesh.platform.billing.service import lock_account
from opsmesh.shared.db.session import session_transaction
from opsmesh.shared.errors import ConflictError, InvalidArgumentError
from opsmesh.users.access import require_platform_admin
from opsmesh.users.auth.dependencies import CurrentUserDep, DbSession
from opsmesh.users.models import UserProfile

router = APIRouter(prefix="/billing", tags=["billing"])


class BudgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: Decimal | None = Field(ge=0, max_digits=24, decimal_places=8, allow_inf_nan=False)
    warning_ratio: Decimal = Field(
        default=Decimal("0.8"), gt=0, le=1, decimal_places=4, allow_inf_nan=False
    )
    expected_revision: int = Field(ge=0)


@router.get("/budgets/{user_id}", operation_id="billing.budgets.list")
def budgets(user_id: UUID, session: DbSession, user: CurrentUserDep) -> list[dict[str, object]]:
    if user_id != user.user_id:
        require_platform_admin(user)
    return [
        {
            "currency": r.currency,
            "amount": r.amount,
            "warning_ratio": r.warning_ratio,
            "revision": r.revision,
        }
        for r in session.scalars(
            select(SpendingLimit)
            .where(SpendingLimit.user_id == user_id)
            .order_by(SpendingLimit.currency)
        )
    ]


@router.put("/budgets/{user_id}/{currency}", operation_id="billing.budgets.set")
def set_budget(
    user_id: UUID, currency: str, payload: BudgetRequest, session: DbSession, user: CurrentUserDep
) -> dict[str, object]:
    require_platform_admin(user)
    if (
        len(currency) != 3
        or not currency.isascii()
        or not currency.isalpha()
        or not currency.isupper()
    ):
        raise InvalidArgumentError("Currency must be a three-letter uppercase code")
    with session_transaction(session):
        lock_account(session, user_id)
        if session.get(UserProfile, user_id) is None:
            raise InvalidArgumentError("The budget user does not exist")
        row = session.get(SpendingLimit, (user_id, currency), with_for_update=True)
        if payload.expected_revision != (row.revision if row else 0):
            raise ConflictError("The budget revision changed")
        if row is None:
            row = SpendingLimit(user_id=user_id, currency=currency, revision=1)
            session.add(row)
        else:
            row.revision += 1
        row.amount, row.warning_ratio = payload.amount, payload.warning_ratio
        record_audit(
            session,
            AuditRecord(
                category="billing",
                action="budget.set",
                source="platform",
                actor=AuditActor.from_user(user),
                target_type="user",
                target_id=str(user_id),
            ),
        )
        return {"currency": currency, "revision": row.revision, "amount": row.amount}


@router.get("/usage", operation_id="billing.usage.read")
def usage(
    session: DbSession,
    user: CurrentUserDep,
    period: Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")],
) -> dict[str, object]:
    limits = {
        r.currency: r
        for r in session.scalars(select(SpendingLimit).where(SpendingLimit.user_id == user.user_id))
    }
    accounts = {
        r.currency: r
        for r in session.scalars(
            select(SpendingAccount).where(
                SpendingAccount.user_id == user.user_id, SpendingAccount.period == period
            )
        )
    }
    items: list[dict[str, object]] = []
    for currency in sorted(limits.keys() | accounts.keys()):
        limit, account = limits.get(currency), accounts.get(currency)
        spent, reserved = (account.spent, account.reserved) if account else (Decimal(0), Decimal(0))
        amount = limit.amount if limit else None
        items.append(
            {
                "currency": currency,
                "spent": spent,
                "reserved": reserved,
                "limit": amount,
                "revision": limit.revision if limit else 0,
                "warning": amount is not None and spent + reserved >= amount * limit.warning_ratio
                if limit
                else False,
            }
        )
    return {
        "period": period,
        "basis": "configured_token_prices_and_confirmed_usage",
        "accounts": items,
    }


@router.get("/charges", operation_id="billing.charges.list")
def charges(
    session: DbSession,
    user: CurrentUserDep,
    run_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, object]]:
    query = select(ModelCharge).where(ModelCharge.user_id == user.user_id)
    if run_id is not None:
        query = query.where(ModelCharge.run_id == run_id)
    return [
        {
            "operation_id": r.operation_id,
            "run_id": r.run_id,
            "provider_id": r.provider_id,
            "model_id": r.model_id,
            "currency": r.currency,
            "period": r.period,
            "pricing": r.pricing,
            "reserved": r.reserved,
            "amount": r.amount,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "status": r.status,
        }
        for r in session.scalars(
            query.order_by(ModelCharge.created_at.desc(), ModelCharge.operation_id)
            .limit(limit)
            .offset(offset)
        )
    ]
