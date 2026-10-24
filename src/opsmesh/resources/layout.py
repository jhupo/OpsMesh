"""Resolve service-owned storage namespaces without creating directories or loading settings."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StorageLayout:
    root: Path
    plugin_packages_root: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", self.root.expanduser().resolve())
        if self.plugin_packages_root is not None:
            object.__setattr__(
                self, "plugin_packages_root", self.plugin_packages_root.expanduser().resolve()
            )

    @property
    def directories(self) -> Path:
        return self.root / "directories"

    @property
    def packages(self) -> Path:
        return self.root / "packages"

    @property
    def history(self) -> Path:
        return self.root / "history"

    @property
    def gateway_media(self) -> Path:
        return self.root / "gateway" / "media"

    @property
    def plugin_packages(self) -> Path:
        return (
            self.plugin_packages_root
            if self.plugin_packages_root is not None
            else (self.root / "plugin-packages")
        )

    @property
    def plugin_data(self) -> Path:
        return self.root / "plugins"
