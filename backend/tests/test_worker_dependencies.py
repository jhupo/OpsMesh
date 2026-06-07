from types import SimpleNamespace

import fakeredis

from backend.app.core.config import Settings
from backend.app.workers.dependencies import get_worker_queue


def test_worker_queue_dependency_uses_app_state_redis_client() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    settings = Settings(
        database_url="sqlite:///test.db",
        redis_url="redis://example.test:6379/0",
    )
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                redis_client=redis,
                settings=settings,
            )
        )
    )

    queue = get_worker_queue(request)  # type: ignore[arg-type]

    assert queue.redis is redis
