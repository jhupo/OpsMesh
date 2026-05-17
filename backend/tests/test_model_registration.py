from sqlalchemy.orm import configure_mappers

from backend.app.db import models as registered_models


def test_model_registry_import_configures_all_relationships() -> None:
    assert registered_models.User.__name__ == "User"
    configure_mappers()
