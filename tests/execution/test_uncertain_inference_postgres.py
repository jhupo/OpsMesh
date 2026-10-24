"""Billing uncertainty must not pin a Session; unknown effects must still pin it."""

import pytest

from opsmesh.agents.config.agent import AgentConfig
from opsmesh.agents.manager import AgentDefinition
from opsmesh.execution.instances.schemas import InstanceCreate
from opsmesh.execution.instances.service import create_instance
from opsmesh.execution.runs.models import ExecutionOperation, ExecutionRun
from opsmesh.execution.runs.operations import OperationLedger
from opsmesh.execution.runs.reconciliation import EffectResolution, reconcile_effect
from opsmesh.execution.runs.schemas import RunCreate
from opsmesh.execution.runs.service import AdmissionPending, claim_run, finish_run, submit_run
from opsmesh.execution.sessions.service import create_session
from opsmesh.platform.billing.contracts import ModelCostRequest, ModelPricing
from opsmesh.platform.billing.models import ModelCharge
from opsmesh.users.quotas.models import CAPACITY_KINDS, TokenReservation, UserTokenAccount
from opsmesh.users.service import create_account, current_user


@pytest.mark.parametrize("kind", ["inference", "model", "tool", "history"])
@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_uncertainty_preserves_reservations_and_correct_session_ordering(
    database_sessions, kind, status
):
    sessions = database_sessions
    cipher = sessions.kw["info"]["dispatch_cipher"]
    definition = AgentDefinition(id="test", config=AgentConfig(name="Test"))
    with sessions.begin() as session:
        user = create_account(
            session,
            email="fifo@example.com",
            display_name="FIFO",
            password="test-password-123456",
            capacity_limits={key: None for key in CAPACITY_KINDS},
            platform_roles=frozenset({"admin"}),
        )
        user_id = user.id
        actor = current_user(session, user_id)
        instance = create_instance(
            session,
            actor=actor,
            cipher=cipher,
            request=InstanceCreate(agent_id=definition.id, request_key="instance"),
            load=lambda _: definition,
        )
        conversation = create_session(session, actor=actor, instance_id=instance.id)
        run_ids = [
            submit_run(
                session,
                actor=actor,
                cipher=cipher,
                request=RunCreate(
                    instance_id=instance.id,
                    session_id=conversation.id,
                    driver="direct",
                    request_key=str(index),
                    input="Hello",
                ),
            ).id
            for index in range(2)
        ]
    with sessions.begin() as session:
        context = claim_run(session, run_id=run_ids[0], driver="direct", cipher=cipher)[0]
    ledger = OperationLedger(sessions, cipher, context)
    if kind in {"inference", "model"}:
        ledger.claim(
            "uncertain",
            kind,
            {},
            tokens=100,
            cost=ModelCostRequest(
                input_bound=80,
                output_bound=20,
                pricing=ModelPricing(input_per_million="1", output_per_million="2"),
            ),
        )
    else:
        ledger.claim("uncertain", kind, {})
    ledger.uncertain("uncertain", error_code="TimeoutError")
    with sessions.begin() as session:
        row = finish_run(session, context=context, status=status, cipher=cipher)
        assert row.status == (status if kind == "inference" else "needs_attention")
        assert row.error_code == (
            "model_usage_unknown" if kind == "inference" else "external_effect_unknown"
        )
        assert row.capacity_id is None
    with sessions.begin() as session:
        receipt = session.get(ExecutionOperation, "uncertain")
        assert receipt.status == "uncertain"
        if kind in {"inference", "model"}:
            assert session.get(TokenReservation, receipt.reservation_id).status == "reserved"
            charge = session.get(ModelCharge, receipt.id)
            assert charge.status == "reserved" and charge.reserved > 0
            assert session.get(UserTokenAccount, user_id).reserved_tokens == 100
        if kind == "inference":
            claim_run(session, run_id=run_ids[1], driver="direct", cipher=cipher)
        else:
            with pytest.raises(AdmissionPending):
                claim_run(session, run_id=run_ids[1], driver="direct", cipher=cipher)
    if kind == "inference":
        with sessions.begin() as session:
            actor = current_user(session, user_id)
            reconciled = reconcile_effect(
                session,
                actor=actor,
                run_id=run_ids[0],
                cipher=cipher,
                request=EffectResolution(
                    operation_id="uncertain",
                    outcome="executed",
                    actual_tokens=10,
                    input_tokens=7,
                    output_tokens=3,
                    evidence="Provider usage receipt",
                ),
            )
            assert reconciled.status == status and reconciled.error_code == "effects_reconciled"
        with sessions() as session:
            assert session.get(UserTokenAccount, user_id).reserved_tokens == 0
            assert session.get(UserTokenAccount, user_id).used_tokens == 10
            assert session.get(ModelCharge, "uncertain").status == "settled"
            assert session.get(ExecutionRun, run_ids[1]).status == "running"
