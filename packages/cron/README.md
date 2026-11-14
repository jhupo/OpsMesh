# Cron

`require("cron")` loads the PostgreSQL-backed timer plugin. Migrate the plugin
schema with the database command before starting it. One scheduler holds an
exclusive PostgreSQL advisory lock for its configured namespace; reserve one
database connection for that lock.

```python
from opsmesh import require

cron = require("cron")
digest = cron.on_cron("0 9 * * mon-fri", name="digest", timezone="Asia/Shanghai")


@digest.handle()
async def publish_digest() -> None:
    await publish_application_digest()
```

The handler is an ordinary framework Matcher. Declare dependencies using
`Depends`, and obtain `ScheduledEvent` by normal event injection when needed.
Timer events do not carry an invented user or support message replies.

For dynamic schedules, declare `on_schedule(name, payload=YourPydanticModel)`
then call `add_schedule(ScheduleDefinition(...))` from the owning plugin.
`CronDefinition`, `IntervalDefinition` and `DateDefinition` use native
APScheduler triggers. Interval anchors and date instants must include a timezone.
Dynamic definitions may omit `timeout_seconds` and
`misfire_grace_seconds`; the plugin configuration supplies those values while
the definition is validated and persisted. Code declarations use the same
resolver, so both declaration styles have one source of defaults. Persisted
values remain stable when configuration changes; old records that omitted a
value are resolved with the current configuration. Explicit definition values
always take precedence.
Use `get_schedule`, `list_schedules`, `update_schedule`, `pause_schedule`,
`resume_schedule`, `delete_schedule` and `reset_schedule` for management.
Operations are scoped to the calling plugin; no client-supplied owner is accepted.

Code defaults initialize missing records. A restart preserves pauses and online
changes; `reset_schedule` explicitly restores current code defaults. Removed or
stopped handlers leave definitions blocked. Timers bind the framework plugin
instance and activation generation; callbacks from before a restart cannot enter
new resources. Changes commit before runtime
synchronization, and returned views expose synchronization failures. No business
HTTP endpoint is mounted; applications may expose their own authorized commands.

Code declaration names and handler keys cannot be renamed online. Dynamic
definitions may change their names and reference another declared handler.
Invalid persisted definitions remain queryable: views retain `id`, `name`, owner
and source, with `definition=None`, `runnable=False`, and
`blocked_reason="invalid definition"`. Replace or delete dynamic definitions;
reset code declarations by their fixed names to restore valid defaults.

Schedules resume at future trigger times after downtime, without replay. One-shot
definitions already past on startup expire. Failures are logged without automatic
retry. Schedule handlers must make their own external effects idempotent. This
plugin provides timers, not durable execution or distributed failover.
