"""Notice event observer plugin demonstrating non-blocking MatcherGroup."""

from opsmesh.adapters import Event, Gateway
from opsmesh.plugin import MatcherGroup, PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="告警观察器",
    description="监听 notice 类型告警事件并通过 Gateway.invoke 上报归档",
    usage="自动监听 notice 事件",
    type="application",
)

alerts = MatcherGroup(priority=5)
alert_notice = alerts.on_notice()


@alert_notice.handle()
async def record_alert(gateway: Gateway, event: Event) -> None:
    await gateway.invoke(
        "record_alert",
        event_name=event.get_event_name(),
        detail=event.get_event_description(),
    )
    await alert_notice.send(f"alert recorded: {event.get_event_description()}")
