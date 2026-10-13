"""Verify probing releases database capacity and rejects an outdated provider snapshot."""

import asyncio
import os

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from opsmesh.agents.providers.activities import ModelProviderHealthActivities
from opsmesh.agents.providers.models import ModelProviderCredential
from opsmesh.agents.providers.probes import ModelProviderHealthCheckResult
from opsmesh.runtime.temporal.activities import ActivityDependencies
from opsmesh.runtime.temporal.contracts import ExecutionKind, ExecutionRequest
from opsmesh.shared.config import Settings
from opsmesh.shared.db.base import Base
from tests.fixtures.database import temporary_postgres_schema
from tests.fixtures.model_provider import seed_default_model_provider
from tests.fixtures.temporal import seed_workspace

pytestmark = pytest.mark.skipif(
    not os.getenv("OPSMESH_TEST_POSTGRES_URL"), reason="PostgreSQL required"
)


@pytest.mark.parametrize("change_configuration", [False, True])
def test_health_probe_releases_connection_and_revalidates_configuration(
    monkeypatch: pytest.MonkeyPatch, change_configuration: bool
) -> None:
    with temporary_postgres_schema() as engine:
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory() as session:
            user, workspace = seed_workspace(session)
            seed_default_model_provider(session, workspace_id=workspace.id, user_id=user.id)
            credential = session.scalar(
                select(ModelProviderCredential).where(
                    ModelProviderCredential.workspace_id == workspace.id
                )
            )
            assert credential is not None
            credential_id = credential.id
            request = ExecutionRequest(
                workspace_id=workspace.id,
                requested_by_user_id=user.id,
                resource_id=credential_id,
                kind=ExecutionKind.MODEL_PROVIDER_HEALTH,
            )

        def change_provider() -> None:
            with factory.begin() as session:
                credential = session.get(ModelProviderCredential, credential_id)
                assert credential is not None
                credential.default_model = "a-new-model"

        async def probe(*args, **kwargs):
            assert engine.pool.checkedout() == 0
            if change_configuration:
                await asyncio.to_thread(change_provider)
            await asyncio.sleep(0)
            assert engine.pool.checkedout() == 0
            return ModelProviderHealthCheckResult(status="healthy", checks=())

        monkeypatch.setattr("opsmesh.agents.providers.activities.probe_model_provider", probe)
        activities = ModelProviderHealthActivities(
            ActivityDependencies(sessions=factory, settings=Settings(environment="test"))
        )
        if change_configuration:
            with pytest.raises(ValueError, match="configuration changed"):
                asyncio.run(activities.check_health(request))
        else:
            asyncio.run(activities.check_health(request))
        with factory() as session:
            credential = session.get(ModelProviderCredential, credential_id)
            assert credential is not None
            assert credential.health_status == ("unknown" if change_configuration else "healthy")
