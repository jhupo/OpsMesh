"""PostgreSQL connections and declarative plugin schema ownership."""

from opsmesh import get_driver
from opsmesh.plugin import PluginMetadata, get_plugin_by_module_name

from .config import Settings
from .connections import (
    Database,
    _shutdown,
    _startup,
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
    config_file="config.json",
)

if get_plugin_by_module_name(__name__) is not None:
    get_driver().on_startup(_startup)
    get_driver().on_shutdown(_shutdown)

__all__ = [
    "Base",
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
