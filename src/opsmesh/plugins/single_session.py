from collections.abc import AsyncGenerator

from opsmesh.adapters import Event
from opsmesh.exception import IgnoredException
from opsmesh.message import event_preprocessor
from opsmesh.params import Depends
from opsmesh.plugin import PluginMetadata

__plugin_meta__ = PluginMetadata(
    name="唯一会话",
    description="限制同一会话内同时只能运行一个响应器",
    usage="加载插件后自动生效",
    type="application",
    homepage="https://github.com/jhupo/OpsMesh",
    config=None,
    supported_adapters=None,
)

_running_matcher: dict[str, int] = {}


async def matcher_mutex(event: Event) -> AsyncGenerator[bool, None]:
    result = False
    try:
        session_id = event.get_session_id()
    except Exception:
        yield result
    else:
        current_event_id = id(event)
        if event_id := _running_matcher.get(session_id):
            result = event_id != current_event_id
        else:
            _running_matcher[session_id] = current_event_id
        try:
            yield result
        finally:
            if not result:
                _running_matcher.pop(session_id, None)


@event_preprocessor
async def preprocess(mutex: bool = Depends(matcher_mutex)) -> None:
    if mutex:
        raise IgnoredException("Another matcher running")
