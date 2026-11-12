"""PostgreSQL connections and declarative plugin schema ownership."""

from opsmesh.framework.plugins import PluginMetadata

from .config import CONFIG_NAMESPACE, Settings
from .connections import (
    Database,
    get_database,
    get_engine,
    get_sessionmaker,
    session,
    transaction,
)
from .dependencies import DatabaseAccess
from .errors import (
    DatabaseError,
    DatabaseUnavailableError,
    MigrationError,
    MigrationRequiredError,
)
from .migrations import check_migrations, run_migrations, validate_migrations
from .models import NAMING_CONVENTION, Base
from .schema import DatabaseSchema

__plugin_meta__ = PluginMetadata(
    name="database",
    description="One PostgreSQL engine, scoped sessions, and owned migrations",
    usage="Require database and declare DatabaseSchema in plugin metadata.",
    type="library",
    config=Settings,
)

__all__ = [
    "Base",
    "CONFIG_NAMESPACE",
    "Database",
    "DatabaseAccess",
    "DatabaseError",
    "DatabaseSchema",
    "DatabaseUnavailableError",
    "MigrationError",
    "MigrationRequiredError",
    "NAMING_CONVENTION",
    "Settings",
    "check_migrations",
    "get_database",
    "get_engine",
    "get_sessionmaker",
    "run_migrations",
    "session",
    "transaction",
    "validate_migrations",
]
