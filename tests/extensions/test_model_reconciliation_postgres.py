"""One-shot recovery retains unknown Token holds and never repeats model I/O."""

from uuid import uuid4

import pytest

from opsmesh.identity.provisioning import UserIdentityDirectory
from opsmesh.identity.quotas import service as quotas
from opsmesh.identity.quotas.models import UserCapacityAccount, UserTokenAccount
from opsmesh.identity.quotas.schemas import NormalizedUsage
from opsmesh.identity.users.service import create_user
from opsmesh.orchestration.contracts import ExecutionBindings, RejectingScopeAdmission
from opsmesh.orchestration.execution.contracts import ModelConnection
from opsmesh.orchestration.runs.model_activities import ExecutionModelProvider
from opsmesh.orchestration.runs.model_operations import (
    ModelOperation,
    ModelOperationReconciliation,
    ModelOperationRequest,
    ModelOperations,
)
from opsmesh.shared.errors import ConflictError
from opsmesh.shared.security.encryption import SecretCipher


@pytest.mark.parametrize("outcome", ["succeeded", "not_executed"])
@pytest.mark.parametrize("kind", ["complete", "embed"])
def test_started_model_recovery_never_reexecutes_and_settles_by_evidence(
    database_sessions, outcome, kind
):
    cipher = SecretCipher(key="JCxBK6PRTTz8-XJDf7ZEbEzcB2s3LFj8nRPODPwf6C0=", key_id="test")
    source_id = uuid4()
    admitted = []

    def source_token_limit(session, **kwargs):
        admitted.append(kwargs)

    class SourceAuthorization:
        def authorize_submission(self, session, *, principal_id, request):
            raise AssertionError("This ledger test seeds an already admitted operation")

        def admit(self, session, *, principal_id, request):
            assert request.source_ref == source_id

    class Connections:
        def resolve(self, session, **kwargs):
            return ModelConnection("unused-test-key", "https://api.openai.com/v1", 1000)

    with database_sessions() as session, session.begin():
        user = create_user(
            session,
            email="s8-model-ledger@example.com",
            display_name="S8 ledger",
            platform_roles=frozenset({"admin"}),
        )
        quotas.ensure_ledger(session, user_id=user.id, token_limit=10000)
        principal = UserIdentityDirectory().load_identity(session, user.id)
        quotas.ensure_capacity_accounts(
            session, user_id=user.id, limits={"concurrency": 1, "environment": 1}
        )
        request = ModelOperationRequest(
            connection_id=uuid4(),
            model="ledger-model",
            kind=kind,
            inputs=["test", "second"],
            max_output_tokens=32,
            source_ref=source_id,
        )
        record = ModelOperation(
            principal_id=user.id,
            source_ref=source_id,
            request_ciphertext=cipher.encrypt_payload(request.model_dump(mode="json")).ciphertext,
        )
        session.add(record)
        session.flush()
        identifier, user_id = record.id, user.id
    operations = ModelOperations(
        ExecutionModelProvider(
            database_sessions,
            ExecutionBindings(tokens=quotas, capacity=quotas, limit_operation=source_token_limit),
            RejectingScopeAdmission(),
            Connections(),
            cipher,
        ),
        SourceAuthorization(),
    )
    assert operations.claim(identifier) is not None
    assert operations.claim(identifier) is None
    assert admitted == [
        {
            "run_ref": str(identifier),
            "operation_id": f"model-operation:{identifier}",
            "tokens": 1032 if kind == "complete" else 74,
        }
    ]
    with database_sessions() as session:
        assert session.get(ModelOperation, identifier).status == "needs_attention"
        assert session.get(UserTokenAccount, user_id).reserved_tokens == (
            1032 if kind == "complete" else 74
        )
        assert session.get(UserCapacityAccount, (user_id, "concurrency")).reserved_value == 0
    usage = NormalizedUsage(input_tokens=10, output_tokens=3) if outcome == "succeeded" else None
    resolution = ModelOperationReconciliation(
        expected_revision=2,
        outcome=outcome,
        actual_usage=usage,
        result={"confirmed": True},
        evidence={"provider_receipt": "test"},
    )
    with database_sessions() as session, pytest.raises(ConflictError), session.begin():
        operations.reconcile(
            session,
            session.get(ModelOperation, identifier, with_for_update=True),
            resolution.model_copy(update={"expected_revision": 3}),
            actor=principal,
        )
    with database_sessions() as session, session.begin():
        operations.reconcile(
            session,
            session.get(ModelOperation, identifier, with_for_update=True),
            resolution,
            actor=principal,
        )
    with database_sessions() as session:
        account = session.get(UserTokenAccount, user_id)
        assert account.reserved_tokens == 0
        assert account.used_tokens == (13 if outcome == "succeeded" else 0)

    assert operations.claim(identifier) is None
