import pytest

from opsmesh.bootstrap.application import OpsMeshApplication
from opsmesh.bootstrap.settings import OpsMeshSettings
from opsmesh.entrypoints.http.app import create_http_app
from opsmesh.framework.errors import OpsMeshError


async def test_serve_requires_explicit_schema_initialization_and_closes_on_failure(tmp_path):
    application = OpsMeshApplication(OpsMeshSettings(data_directory=tmp_path, worker_enabled=False))
    api = create_http_app(application)
    with pytest.raises(OpsMeshError) as error:
        async with api.router.lifespan_context(api):
            pytest.fail("Serving an uninitialized schema must fail")
    assert error.value.code == "SCHEMA_NOT_INITIALIZED"
    assert application.services.http_client.is_closed
