"""Associate schedule declarations with the framework's native Matcher."""

from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel, JsonValue, TypeAdapter

from opsmesh import get_current_plugin, get_plugin_config, on
from opsmesh.matchers import Matcher
from opsmesh.plugins.registry import track_registration
from opsmesh.rules import Rule

from .config import Settings
from .errors import ScheduleError
from .events import ScheduledEvent
from .schemas import (
    CronDefinition,
    DateDefinition,
    IntervalDefinition,
    ScheduleDefinition,
    ScheduleName,
    TriggerDefinition,
)


@dataclass(frozen=True)
class ScheduleDeclaration:
    """A named Matcher binding with payload validation and a default plan."""

    owner: str
    name: str
    matcher: type[Matcher]
    payload_type: type[BaseModel] | None = None
    default: ScheduleDefinition | None = None

    def validate_payload(
        self, value: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        """Validate only JSON payload accepted by this declaration."""
        if self.payload_type is not None:
            return self.payload_type.model_validate(value).model_dump(
                mode="json"
            )
        if value:
            raise ScheduleError("This handler does not accept payload fields")
        return {}


declarations: dict[tuple[str, str], ScheduleDeclaration] = {}


def _declare(
    name: str,
    payload_type: type[BaseModel] | None = None,
    default: ScheduleDefinition | None = None,
) -> type[Matcher]:
    name = TypeAdapter(ScheduleName).validate_python(name)
    plugin = get_current_plugin()
    if plugin is None:
        raise ScheduleError("Scheduled handlers must be declared by a plugin")
    key = (plugin.id_, name)
    if key in declarations:
        raise ScheduleError(f"Schedule handler {name!r} is already declared")

    async def matches(event: ScheduledEvent) -> bool:
        return (
            isinstance(event, ScheduledEvent)
            and event.owner_plugin == plugin.id_
        )

    matcher = on("scheduled", rule=Rule(matches), block=True)
    declaration = ScheduleDeclaration(
        plugin.id_, name, matcher, payload_type, default
    )
    declarations[key] = declaration
    track_registration(lambda: declarations.pop(key, None))
    return matcher


def on_schedule(
    name: str, *, payload: type[BaseModel] | None = None
) -> type[Matcher]:
    """Declare a handler that dynamic schedules may reference."""
    return _declare(name, payload)


def _default(name: str, trigger: TriggerDefinition) -> ScheduleDefinition:
    settings = get_plugin_config(Settings)
    return ScheduleDefinition(
        name=name,
        handler=name,
        trigger=trigger,
        timeout_seconds=settings.timeout_seconds,
        misfire_grace_seconds=settings.misfire_grace_seconds,
    )


def on_cron(
    expression: str, *, name: str, timezone: str | None = None
) -> type[Matcher]:
    """Declare a persistent five-field cron default for this plugin."""
    trigger = CronDefinition(
        expression=expression,
        timezone=timezone or get_plugin_config(Settings).timezone,
    )
    trigger.build()
    return _declare(
        name,
        default=_default(name, trigger),
    )


def on_interval(
    seconds: float, *, name: str, start_at: datetime | None = None
) -> type[Matcher]:
    """Declare an interval; its initial anchor is persisted only once."""
    trigger = IntervalDefinition(
        seconds=seconds, start_at=start_at or datetime.now(UTC)
    )
    return _declare(
        name,
        default=_default(name, trigger),
    )


def on_date(run_at: datetime, *, name: str) -> type[Matcher]:
    """Declare a single scheduled invocation."""
    trigger = DateDefinition(run_at=run_at)
    return _declare(
        name,
        default=_default(name, trigger),
    )
