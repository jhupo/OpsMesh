"""Assemble the application's SQLAlchemy model registry before opening sessions."""


def load_models() -> None:
    from opsmesh.agents import models as agents
    from opsmesh.catalog import models as catalog
    from opsmesh.gateway import models as gateway
    from opsmesh.plugins import models as plugins
    from opsmesh.quotas import models as quotas
    from opsmesh.resources import models as resources
    from opsmesh.sessions import models as sessions
    from opsmesh.shared import models as shared
    from opsmesh.tasks import models as tasks
    from opsmesh.users import models as users

    _ = agents, catalog, gateway, plugins, quotas, resources, sessions, shared, tasks, users
