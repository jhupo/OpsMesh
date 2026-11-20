import re
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path


class Fault(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, message, code="invalid_request"):
    if not condition:
        raise Fault(code, message)


def identifier(value):
    require(
        isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value),
        "Invalid identifier",
    )
    return value


@dataclass(frozen=True)
class Settings:
    data: Path = Path("/var/lib/opsmesh")
    run: Path = Path("/run/opsmesh")
    logs: Path = Path("/var/log/opsmesh")
    inbox: Path = Path("/var/lib/opsmesh-inbox")
    backups: Path = Path("/var/backups/opsmesh")
    group: str = "opsmesh-admin"
    driver: str = "~none"
    protected: tuple[str, ...] = ()
    restart_safe: tuple[str, ...] = ()

    @classmethod
    def load(cls, path):
        with Path(path).open("rb") as f:
            values = tomllib.load(f)
        require(
            values.keys() <= {f.name for f in fields(cls)},
            "Unknown service setting",
        )
        for key in ("protected", "restart_safe"):
            if key in values:
                require(isinstance(values[key], list), f"Invalid {key} list")
                values[key] = tuple(identifier(name) for name in values[key])
        paths = []
        for key in ("data", "run", "logs", "inbox", "backups"):
            p = Path(values.get(key, getattr(cls, key)))
            require(
                p.is_absolute() and p != Path(p.anchor),
                f"Invalid {key} directory",
            )
            values[key] = p.resolve()
            require(
                all(
                    not (
                        q.is_relative_to(values[key])
                        or values[key].is_relative_to(q)
                    )
                    for q in paths
                ),
                "Service directories must not overlap",
            )
            paths.append(values[key])
        return cls(**values)

    @property
    def sock(self):
        return self.run / "opsmesh.sock"
