from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from opsmesh.infra.database import connect
from opsmesh.infra.encryption import SecretCipher
from opsmesh.plugins.loader import close
from opsmesh.plugins.registry import PluginRegistry
from opsmesh.shared.logging import configure as configure_logging
from opsmesh.shared.tracing import configure as configure_tracing

from .schema import load_models
from .settings import Settings


@dataclass
class Application:
    settings: Settings
    sessions: sessionmaker
    cipher: SecretCipher
    registry: PluginRegistry

    @classmethod
    def create(cls, settings: Settings | None = None) -> "Application":
        config = settings or Settings()
        config.data_dir = Path(config.data_dir).resolve()
        config.data_dir.mkdir(parents=True, exist_ok=True)
        configure_logging(
            config.log_dir or config.data_dir / "logs",
            config.log_level,
            console=config.log_console,
            file=config.log_file,
            max_bytes=config.log_max_bytes,
            backup_count=config.log_backup_count,
        )
        configure_tracing()
        load_models()
        return cls(
            config,
            connect(config.database_url.get_secret_value()),
            SecretCipher(config.encryption_key.get_secret_value()),
            PluginRegistry(),
        )

    async def close(self) -> None:
        try:
            await close(self.registry)
        finally:
            engine = self.sessions.kw["bind"]
            engine.dispose()
