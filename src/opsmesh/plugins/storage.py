"""Persist host-owned plugin settings independently of runtime objects."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .errors import PluginProtectedError

SCHEMA_VERSION = 2


class PluginRecord(BaseModel):
    """One plugin's load location, startup settings, and configuration."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", hide_input_in_errors=True
    )

    module: str
    enabled: bool = False
    autoload: bool = False
    required: bool = False
    protected: bool = False
    config: dict[str, JsonValue] = Field(default_factory=dict, repr=False)
    directory: str | None = None

    @model_validator(mode="after")
    def validate_protection(self) -> Self:
        """Require protected plugins to participate in every host startup."""
        if self.protected and not (
            self.enabled and self.autoload and self.required
        ):
            raise ValueError(
                "Protected plugins must be enabled, autoloaded, and required"
            )
        return self

    @field_validator("module")
    @classmethod
    def validate_module(cls, value: str) -> str:
        """Accept absolute Python module names only."""
        if not value or any(
            not part.isidentifier() for part in value.split(".")
        ):
            raise ValueError("Plugin module must be a dotted Python name")
        return value

    @field_validator("directory")
    @classmethod
    def validate_directory(cls, value: str | None) -> str | None:
        """Persist import roots independently of the working directory."""
        if value is not None and not Path(value).is_absolute():
            raise ValueError("Plugin directory must be absolute")
        return str(Path(value).resolve()) if value is not None else None


class PluginStorage:
    """Persist plugin configuration without managing host processes.

    Connections are short-lived and never cross threads. Async callers use
    a worker thread; no transaction remains open while plugin code runs.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "state.sqlite3"
        self._opened = False

    def __enter__(self) -> Self:
        self.root.mkdir(parents=True, exist_ok=True)
        self._opened = True
        try:
            self._initialize()
        except BaseException:
            self._opened = False
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self._opened = False

    def _connect(self) -> sqlite3.Connection:
        if not self._opened:
            raise RuntimeError("Plugin storage is not open")
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise RuntimeError("Unsupported host database schema")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS plugins ("
                "module TEXT PRIMARY KEY, "
                "record TEXT NOT NULL CHECK(json_valid(record)), "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            if version == 1:
                for row in connection.execute(
                    "SELECT module, record FROM plugins"
                ).fetchall():
                    values = json.loads(row[1])
                    values["protected"] = False
                    record = PluginRecord.model_validate(values)
                    connection.execute(
                        "UPDATE plugins SET record=? WHERE module=?",
                        (record.model_dump_json(), row[0]),
                    )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def list(self) -> list[PluginRecord]:
        """Read independent records in deterministic module order."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT record FROM plugins ORDER BY module"
            )
            return [PluginRecord.model_validate_json(row[0]) for row in rows]

    def get(self, module: str) -> PluginRecord:
        """Read one record or report an unknown plugin."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT record FROM plugins WHERE module = ?", (module,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Plugin is not registered: {module}")
        return PluginRecord.model_validate_json(row[0])

    def save(self, record: PluginRecord) -> None:
        """Atomically replace a validated catalog record."""
        record = PluginRecord.model_validate(record.model_dump())
        payload = json.dumps(record.model_dump(mode="json"), allow_nan=False)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT record FROM plugins WHERE module=?", (record.module,)
            ).fetchone()
            if previous is not None:
                stored = PluginRecord.model_validate_json(previous[0])
                if stored.protected != record.protected:
                    raise PluginProtectedError(
                        "Protection changes require offline maintenance"
                    )
            connection.execute(
                "INSERT INTO plugins(module, record) VALUES (?, ?) "
                "ON CONFLICT(module) DO UPDATE SET "
                "record=excluded.record, updated_at=CURRENT_TIMESTAMP",
                (record.module, payload),
            )

    def delete(self, module: str) -> None:
        """Remove a persisted plugin record without deleting its files."""
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT record FROM plugins WHERE module=?", (module,)
            ).fetchone()
            if (
                row is not None
                and PluginRecord.model_validate_json(row[0]).protected
            ):
                raise PluginProtectedError(
                    "Protected plugin records cannot be removed"
                )
            connection.execute(
                "DELETE FROM plugins WHERE module = ?", (module,)
            )

    def set_protected(self, module: str) -> None:
        """Protect a record during exclusive offline maintenance."""
        record = self.get(module)
        values = record.model_dump()
        values["protected"] = True
        protected = PluginRecord.model_validate(values)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE plugins SET record=? WHERE module=?",
                (protected.model_dump_json(), module),
            )
